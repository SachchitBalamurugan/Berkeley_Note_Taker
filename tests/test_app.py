import io
import os
import tempfile
import unittest
from app import create_app


class WhiteboardApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "DATABASE": os.path.join(self.temp.name, "test.db"), "UPLOAD_FOLDER": os.path.join(self.temp.name, "uploads")})
        self.client = self.app.test_client()

    def tearDown(self): self.temp.cleanup()
    def post(self, url, body): return self.client.post(url, json=body)

    def test_notebook_page_note_and_search(self):
        notebook = self.post('/api/notebooks', {'title':'Physics'}).get_json()
        page = self.post(f"/api/notebooks/{notebook['id']}/pages", {'title':'Momentum'}).get_json()
        note = self.post(f"/api/pages/{page['id']}/items", {'kind':'note','content':'Force equals mass times acceleration'}).get_json()
        self.assertEqual(note['kind'], 'note')
        self.assertEqual(self.client.get('/api/search?q=acceleration').get_json()[0]['page_id'], page['id'])
        loaded = self.client.get(f"/api/pages/{page['id']}").get_json()
        self.assertEqual(loaded['items'][0]['content'], 'Force equals mass times acceleration')

    def test_strokes_and_bad_uploads(self):
        notebook = self.post('/api/notebooks', {'title':'Art'}).get_json()
        page = self.post(f"/api/notebooks/{notebook['id']}/pages", {'title':'Sketches'}).get_json()
        response = self.post(f"/api/pages/{page['id']}/strokes", {'tool':'pen','color':'#000000','width':4,'points':[{'x':1,'y':2},{'x':3,'y':4}]})
        self.assertEqual(response.status_code, 201)
        response = self.client.post(f"/api/pages/{page['id']}/upload", data={'image':(io.BytesIO(b'not image'),'bad.txt')}, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__': unittest.main()
