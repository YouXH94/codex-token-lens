import io,json,gzip,tempfile,unittest,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lens
class PreparedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); p=Path(self.tmp.name)
        self.app=lens.App(lens.Store(p/'state/db',[{'path':str(p/'missing'),'label':'Test'}],min_free_mib=0))
    def tearDown(self): self.tmp.cleanup()
    def fetch(self,extra=None):
        cls=self.app.handler(); h=cls.__new__(cls); h.path='/api/report';h.command='GET'
        h.request_version='HTTP/1.1';h.requestline='GET /api/report HTTP/1.1'
        h.headers={'Host':'127.0.0.1:8765','Cookie':'lens_token='+self.app.token,**(extra or {})}
        h.server=SimpleNamespace(server_port=8765);h.wfile=io.BytesIO()
        h.do_GET();return h.wfile.getvalue().split(b'\r\n\r\n',1)
    def test_compression_is_authenticated_and_decodable(self):
        headers,body=self.fetch({'Accept-Encoding':'gzip'})
        self.assertIn(b'Content-Encoding: gzip',headers)
        self.assertEqual(json.loads(gzip.decompress(body))['events'],self.app.latest['events'])
        headers,body=self.fetch({'Cookie':''});self.assertIn(b'403',headers)
    def test_error_transition_and_published_revision(self):
        headers,body=self.fetch();etag=next(x for x in headers.split(b'\r\n') if x.startswith(b'ETag:')).split(b': ',1)[1].decode()
        self.app.revision+=1 # simulate a worker preparing a replacement snapshot
        headers,body=self.fetch({'If-None-Match':etag});self.assertIn(b'304',headers)
        self.app.store.error='synthetic pause';headers,body=self.fetch({'If-None-Match':etag})
        self.assertIn(b'200',headers);self.assertEqual(json.loads(body)['scan_error'],'synthetic pause')
