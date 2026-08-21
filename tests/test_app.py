import io
import os
import tempfile
import unittest

from PIL import Image
from reportlab.pdfgen import canvas

from app import create_app


def sample_pdf(pages=2):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792))
    for number in range(pages):
        pdf.drawString(72, 720, f"Page {number + 1}")
        pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer


def sample_png():
    buffer = io.BytesIO()
    Image.new("RGB", (300, 200), "skyblue").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


class AnnotatorApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "DATABASE": os.path.join(self.temp.name, "test.db"), "UPLOAD_FOLDER": os.path.join(self.temp.name, "uploads")})
        self.client = self.app.test_client()
        self.notebook = self.post('/api/notebooks', {'title': 'Coursework'}).get_json()

    def tearDown(self): self.temp.cleanup()
    def post(self, url, body): return self.client.post(url, json=body)

    def upload_pdf(self, pages=2, name='syllabus.pdf'):
        return self.client.post(f"/api/notebooks/{self.notebook['id']}/documents",
                                data={'document': (sample_pdf(pages), name)}, content_type='multipart/form-data')

    def test_blank_document_gets_one_page_and_search_finds_notes(self):
        document = self.post(f"/api/notebooks/{self.notebook['id']}/pages", {'title': 'Momentum'}).get_json()
        note = self.post(f"/api/pages/{document['id']}/items", {'kind': 'note', 'content': 'Force equals mass times acceleration'}).get_json()
        self.assertEqual(note['kind'], 'note')
        loaded = self.client.get(f"/api/pages/{document['id']}").get_json()
        self.assertEqual(len(loaded['pages']), 1)
        self.assertEqual(loaded['items'][0]['content'], 'Force equals mass times acceleration')
        # An annotation with no page named still lands on the document's first page.
        self.assertEqual(loaded['pages'][0]['items'][0]['id'], note['id'])
        self.assertEqual(self.client.get('/api/search?q=acceleration').get_json()[0]['page_id'], document['id'])

    def test_pdf_upload_renders_a_row_per_page(self):
        response = self.upload_pdf(pages=3)
        self.assertEqual(response.status_code, 201)
        document = response.get_json()
        self.assertEqual(document['title'], 'syllabus')
        self.assertEqual([page['page_number'] for page in document['pages']], [1, 2, 3])
        for page in document['pages']:
            self.assertEqual((page['width'], page['height']), (612.0, 792.0))
            self.assertTrue(os.path.exists(os.path.join(self.app.config['UPLOAD_FOLDER'], page['image_name'])))

    def test_image_upload_becomes_a_single_page_document(self):
        response = self.client.post(f"/api/notebooks/{self.notebook['id']}/documents",
                                    data={'document': (sample_png(), 'scan.png')}, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 201)
        pages = response.get_json()['pages']
        self.assertEqual(len(pages), 1)
        self.assertEqual((pages[0]['width'], pages[0]['height']), (300.0, 200.0))

    def test_annotations_stay_on_the_page_they_were_drawn_on(self):
        document = self.upload_pdf(pages=2).get_json()
        second = document['pages'][1]
        stroke = self.post(f"/api/pages/{document['id']}/strokes", {
            'tool': 'highlighter', 'color': '#ffd400', 'width': 12, 'doc_page_id': second['id'],
            'points': [{'x': 100, 'y': 200}, {'x': 300, 'y': 205}]}).get_json()
        self.post(f"/api/pages/{document['id']}/items", {'kind': 'note', 'content': 'check this', 'doc_page_id': second['id']})
        loaded = self.client.get(f"/api/pages/{document['id']}").get_json()
        self.assertEqual(loaded['pages'][0]['strokes'], [])
        self.assertEqual(loaded['pages'][0]['items'], [])
        self.assertEqual(loaded['pages'][1]['strokes'][0]['id'], stroke['id'])
        self.assertEqual(loaded['pages'][1]['items'][0]['content'], 'check this')

    def test_annotation_rejects_a_page_from_another_document(self):
        first = self.upload_pdf(pages=1, name='one.pdf').get_json()
        second = self.upload_pdf(pages=1, name='two.pdf').get_json()
        response = self.post(f"/api/pages/{first['id']}/strokes", {
            'tool': 'pen', 'color': '#000000', 'width': 4, 'doc_page_id': second['pages'][0]['id'],
            'points': [{'x': 1, 'y': 2}, {'x': 3, 'y': 4}]})
        self.assertEqual(response.status_code, 400)

    def test_blank_page_can_be_appended_and_removed(self):
        document = self.upload_pdf(pages=1).get_json()
        added = self.client.post(f"/api/pages/{document['id']}/blank-page").get_json()
        self.assertEqual(added['page_number'], 2)
        self.assertIsNone(added['image_name'])
        self.assertEqual(self.client.delete(f"/api/doc-pages/{added['id']}").status_code, 204)
        # The last remaining page is protected.
        remaining = self.client.get(f"/api/pages/{document['id']}").get_json()['pages']
        self.assertEqual(len(remaining), 1)
        self.assertEqual(self.client.delete(f"/api/doc-pages/{remaining[0]['id']}").status_code, 400)

    def test_export_flattens_annotations_into_a_pdf(self):
        document = self.upload_pdf(pages=2).get_json()
        self.post(f"/api/pages/{document['id']}/strokes", {
            'tool': 'pen', 'color': '#e02424', 'width': 5, 'doc_page_id': document['pages'][0]['id'],
            'points': [{'x': 50, 'y': 60}, {'x': 250, 'y': 300}]})
        self.post(f"/api/pages/{document['id']}/items", {'kind': 'note', 'content': 'Marked up', 'doc_page_id': document['pages'][0]['id']})
        response = self.client.get(f"/api/pages/{document['id']}/export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertTrue(response.data.startswith(b'%PDF'))
        self.assertEqual(self.client.get(f"/api/notebooks/{self.notebook['id']}/export").status_code, 200)

    def test_deleting_a_document_removes_its_rendered_pages(self):
        document = self.upload_pdf(pages=2).get_json()
        images = [os.path.join(self.app.config['UPLOAD_FOLDER'], page['image_name']) for page in document['pages']]
        self.assertEqual(self.client.delete(f"/api/pages/{document['id']}").status_code, 204)
        self.assertFalse(any(os.path.exists(path) for path in images))

    def test_bad_uploads_are_rejected(self):
        response = self.client.post(f"/api/notebooks/{self.notebook['id']}/documents",
                                    data={'document': (io.BytesIO(b'not a document'), 'bad.txt')}, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 400)
        document = self.post(f"/api/notebooks/{self.notebook['id']}/pages", {'title': 'Sketches'}).get_json()
        response = self.client.post(f"/api/pages/{document['id']}/upload",
                                    data={'image': (io.BytesIO(b'not image'), 'bad.txt')}, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
