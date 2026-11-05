"""Offline, deterministic updater regression tests."""
import unittest
from veil.updates import find_update

class UpdateTests(unittest.TestCase):
    def test_newer_public_release(self):
        result=find_update(lambda:[{'tag_name':'v0.11.0', 'draft':False},
                                   {'tag_name':'v0.10.0-beta.1','draft':False}])
        self.assertEqual(result['tag'],'v0.11.0')
        self.assertTrue(result['releases_url'].startswith('https://github.com/DumbAssit21/VEIL/'))
    def test_drafts_and_bad_tags_are_ignored(self):
        self.assertIsNone(find_update(lambda:[{'tag_name':'v99.0.0','draft':True},
            {'tag_name':'not-a-version','draft':False}]))
    def test_rejects_bad_response(self):
        with self.assertRaises(RuntimeError):find_update(lambda:{'error':'bad'})
    def test_upstream_urls_are_not_accepted(self):
        result=find_update(lambda:[{'tag_name':'v0.12.0','draft':False,
            'html_url':'https://attacker.example/evil.exe'}])
        self.assertEqual(result['releases_url'],'https://github.com/DumbAssit21/VEIL/releases')

if __name__=='__main__':unittest.main()
