"""Offline checks for fork behavior retained across upstream metadata changes."""
import tempfile
import unittest
from argparse import Namespace
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
        from zotify import api, metadata
        cls.api = api
        cls.metadata = metadata

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def setUp(self):
        self.query = self.api.Query('offline-test')

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
