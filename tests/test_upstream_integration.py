"""Offline checks for fork behavior retained across upstream metadata changes."""
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from zotify.config import Config, Zotify


class UpstreamIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        Config.load(Namespace(config_location=cls.tempdir.name, debug=False,
                              test_mode=True, update_config=False,
                              update_archive=False, verify_library=False))
        Config.Values['ROOT_PATH'] = cls.tempdir.name
        Config.Values['SONG_ARCHIVE_LOCATION'] = cls.tempdir.name
        from zotify import api, metadata
        cls.api = api
        cls.metadata = metadata

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def setUp(self):
        self.query = self.api.Query('offline-test')

    def test_fast_skip_fetches_only_missing_tracks_and_preserves_order(self):
        done, missing = 'track:done', 'track:missing'
        self.query.context.completed_downloads = {done: Path('/missing-on-purpose.mp3')}
        response = {'uri': ':' + missing, 'type': 'track', 'name': 'New track'}
        with Zotify.bind_run_context(self.query.context), \
             patch.object(self.api.Content, '_fetch_uris_metadata', return_value=[response]) as fetch:
            results = self.api.Content.fetch_uris_metadata([done, missing, done], self.api.Track)
        fetch.assert_called_once_with([missing], self.api.Track, None, False)
        self.assertEqual([item['uri'] for item in results], [':' + done, ':' + missing, ':' + done])

    def test_fast_skip_avoids_metadata_requests_when_all_tracks_are_complete(self):
        uri = 'track:done'
        self.query.context.completed_downloads = {uri: Path('/missing-on-purpose.mp3')}
        with Zotify.bind_run_context(self.query.context), \
             patch.object(self.api.Track, 'fetch_metadata') as fetch:
            results = self.api.Content.fetch_uris_metadata([uri], self.api.Track)
        fetch.assert_not_called()
        self.assertEqual(results, [{'uri': ':' + uri, 'type': 'track'}])

    def test_fast_skip_retains_paths_for_each_playlist_without_file_or_journal_checks(self):
        track = self.api.Track('track:done')
        path = Path('/missing-on-purpose.mp3')
        self.query.context.completed_downloads = {track.uri: path}
        playlists = [self.api.Playlist('playlist:one'), self.api.Playlist('playlist:two')]
        with patch.object(Path, 'stat', side_effect=AssertionError('unexpected file check')), \
             patch.object(self.query.context.journal, 'get', side_effect=AssertionError('unexpected journal read')):
            for playlist in playlists:
                stack = self.api.ParentStack([self.query, playlist, track])
                self.assertTrue(track.check_skippable(stack))
                track.download(stack)
                self.assertEqual(track._real_filepaths[stack], path)
        self.assertEqual(self.query._run_skipped, {track.uri})

    def test_fast_skip_options_and_new_run_snapshot(self):
        journal = self.query.context.journal
        with patch.object(journal, 'completed_paths', return_value={'track:done': Path('/done.mp3')}) as load, \
             patch.object(Zotify.CONFIG, 'get_fast_skip', return_value=True), \
             patch.object(Zotify.CONFIG, 'get_skip_existing', return_value=True), \
             patch.object(Zotify.CONFIG, 'get_download_parent_album', return_value=False), \
             patch.object(Zotify.CONFIG, 'get_always_check_lyrics', return_value=False):
            self.query.load_completed_downloads()
            self.assertIn('track:done', self.query.context.completed_downloads)
            for option, value in (('get_fast_skip', False), ('get_skip_existing', False),
                                  ('get_download_parent_album', True), ('get_always_check_lyrics', True)):
                with patch.object(Zotify.CONFIG, option, return_value=value):
                    self.query.load_completed_downloads()
                    self.assertEqual(self.query.context.completed_downloads, {})
        load.assert_called_once()

    def test_archive_instances_are_reused_only_within_a_run(self):
        with Zotify.bind_run_context(self.query.context):
            first = self.metadata.SongArchive()
            with patch.object(first, '_read_entry_strs', return_value=[]) as read:
                first._get_lookup_index()
                self.metadata.SongArchive()._get_lookup_index()
            self.assertIs(first, self.metadata.SongArchive())
            read.assert_called_once()
        other = self.api.Query('other-run')
        with Zotify.bind_run_context(other.context):
            self.assertIsNot(first, self.metadata.SongArchive())

    def test_playlist_recursion_skips_completed_metadata_and_enrichment(self):
        uri = 'track:1111111111111111111111'
        self.query.context.completed_downloads = {uri: Path('/previous-download.mp3')}
        response = {
            'name': 'My playlist', 'uri': ':playlist:abcdefghijklmnopqrstuv',
            'type': 'playlist', 'length': 1,
            'contents': {'items': [{'uri': ':' + uri, 'type': 'track', 'attributes': {}}]},
        }
        with Zotify.bind_run_context(self.query.context), \
             patch.object(self.api.Track, 'fetch_metadata') as fetch, \
             patch.object(self.api.Content, 'fetch_map_fix', wraps=self.api.Content.fetch_map_fix) as fix:
            playlists = self.query.parse_uris_metadata([response], self.api.Playlist, hide_loader=True)
            self.query.requested_objs = [playlists]
            self.query.skip_existing_before_enrichment()
            with patch.object(Zotify.CONFIG, 'get_export_zmd', return_value=False):
                self.query.conditional_metadata()
        fetch.assert_not_called()
        self.assertIn(uri, self.query._run_skipped)
        self.assertEqual(sum(bool(call.args[0]) for call in fix.call_args_list), 1)
        track = playlists[0]._main_items[0]
        self.assertTrue(track._downloaded)

    def test_archive_cache_observes_appended_downloads(self):
        directory = Path(self.tempdir.name) / self.id()
        directory.mkdir()
        archive_path = directory / self.api.LOCAL_SONG_ARCHIVE
        archive_path.touch()
        path = directory / 'track.mp3'
        path.write_bytes(b'audio')
        track = self.api.Track('track:archive-cache')
        with Zotify.bind_run_context(self.query.context):
            archive = self.metadata.SongArchive(directory)
            self.assertIsNone(archive.obj_in_archive(track))
            archive.add_obj(track, path)
            self.assertEqual(self.metadata.SongArchive(directory).obj_in_archive(track), path)

    def test_archive_created_after_initial_lookup_is_enabled(self):
        directory = Path(self.tempdir.name) / self.id()
        directory.mkdir()
        with Zotify.bind_run_context(self.query.context):
            first = self.metadata.SongArchive(directory)
            self.assertTrue(first.disabled)
            (directory / self.api.LOCAL_SONG_ARCHIVE).touch()
            refreshed = self.metadata.SongArchive(directory)
            self.assertIs(first, refreshed)
            self.assertFalse(refreshed.disabled)

    def test_m3u_export_supports_tracks_without_fetched_metadata(self):
        track = self.api.Track('track:done')
        path = Path(self.tempdir.name) / 'done.mp3'
        playlist_path = Path(self.tempdir.name) / 'fast-skip.m3u8'
        playlist = object.__new__(self.metadata.M3U8)
        playlist.path = playlist_path
        playlist.cont_type = self.api.Track
        playlist.name = 'Test playlist'
        with patch.object(self.api.Printer, 'hashtaged'):
            playlist.write([track], [path])
        exported = playlist_path.read_text()
        self.assertIn('#EXTINF:-1,', exported)
        self.assertIn('done.mp3', exported)

    def test_imported_playlist_refreshes_without_discarding_cached_track(self):
        playlist = self.api.Playlist('playlist:abcdefghijklmnopqrstuv')
        playlist.name = 'Old name'
        playlist.length = 1
        track = self.api.Track('track:abcdefghijklmnopqrstuv')
        track.name = 'Cached track'
        playlist._main_items.append(track)
        playlist.adopt(track)
        self.query.parsed_request = [[playlist.uri]] + [[] for _ in range(len(self.api.ITEM_BULK_FETCH) - 1)]

        with patch.object(Zotify.CONFIG, 'get_import_zmd', return_value=True), \
             patch.object(self.metadata.MetadataIO, 'from_zmd'):
            self.query.handle_zmd_prefetch()

        self.assertEqual(self.query.parsed_request[0], [playlist.uri])
        self.assertEqual(playlist._main_items, [])
        self.assertIsNone(playlist.name)
        self.assertEqual(track.name, 'Cached track')
        self.assertNotIn(playlist, track.parents)

        playlist.parse_metadata(None, {
            'name': 'Fresh name', 'uri': ':' + playlist.uri,
            'type': 'playlist', 'length': 1,
            'contents': {'items': [{
                'name': 'New track', 'uri': ':track:2222222222222222222222',
                'type': 'track', 'attributes': {},
            }]},
        })
        self.assertEqual(playlist.name, 'Fresh name')
        self.assertEqual([item.name for item in playlist._main_items], ['New track'])

    def test_bulk_missing_item_uses_single_item_fallback(self):
        uris = ['track:1111111111111111111111', 'track:2222222222222222222222']
        with patch.object(Zotify.CONFIG, 'permit_legacy_api', return_value=False), \
             patch.object(Zotify, 'ALLOW_LIBRE_BULK', True), \
             patch.object(Zotify, 'invoke_libre_bulk_md', return_value=[None, {'name': 'Second'}]), \
             patch.object(self.api.Track, 'fetch_metadata', return_value={'name': 'First', 'uri': ':track:1111111111111111111111', 'type': 'track'}):
            resps = self.api.Content.fetch_uris_metadata(uris, self.api.Track, hide_loader=True)

        self.assertEqual([resp['name'] for resp in resps], ['First', 'Second'])
        self.assertEqual(resps[1]['uri'], ':' + uris[1])

    def test_stream_connection_error_is_handled_without_name_error(self):
        track = self.api.Track('track:3333333333333333333333')
        stack = self.api.ParentStack([self.query, track])
        with patch.object(track, 'fetch_stream', side_effect=ConnectionError('offline')), \
             patch.object(Zotify.CONFIG, 'get_retry_attempts', return_value=0), \
             patch.object(self.api.Printer, 'hashtaged'):
            result = track.fetch_stream_with_recovery(
                self.tempdir.name + '/unwritten-audio', stack)
        self.assertIsNone(result)

    def test_stream_runtime_error_reports_without_name_error(self):
        quality = SimpleNamespace(preferred=SimpleNamespace(name='test'))
        content = SimpleNamespace(id='one', type_attr='track', file_ids=None)
        feeder = SimpleNamespace(load=lambda *args: (_ for _ in ()).throw(RuntimeError(500)))
        session = SimpleNamespace(content_feeder=lambda: feeder)
        with patch.object(Zotify, 'to_libre_content', return_value='one'), \
             patch.object(Zotify, 'current_session', return_value=session), \
             patch.object(Zotify, 'DOWNLOAD_QUALITY', quality), \
             patch.object(Zotify, 'api_status_str', return_value='server error'), \
             patch.object(self.api.Printer, 'hashtaged'), \
             patch.object(self.api.Printer, 'logger'):
            result = Zotify.get_content_stream(content)
        self.assertIsNone(result)

    def test_album_duration_without_disc_numbers(self):
        album = self.api.Album('album:abcdefghijklmnopqrstuv')
        album._update_child_based_metadata()
        self.assertEqual(album.total_discs, 0)
        self.assertEqual(album.duration_ms, 0)


if __name__ == '__main__':
    unittest.main()
