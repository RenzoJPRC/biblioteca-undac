"""Paquete H7 remoto: EOL autorizado, integridad estricta de contenido, sin BD."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from ml.services import retorno_7_operativos_experimental as service

class TestCargaH7Checkout(unittest.TestCase):
    def setUp(self):
        for target in ('pyodbc.connect','pymssql.connect','requests.sessions.Session.request'):
            p=patch(target,side_effect=AssertionError('Servicio real prohibido'));p.start();self.addCleanup(p.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        source=service.DIRECTORY.relative_to(service.ROOT).as_posix()
        for name in ['manifest.json',service.ARTIFACT,'metadatos_candidato.json','preflight.json','constructor_congelado.py','calendario_aplicado.csv','reglas_calendario_fuente.py']:
            blob=subprocess.check_output(['git','show',f'HEAD:{source}/{name}'],cwd=service.ROOT)
            (self.folder/name).write_bytes(blob)

    def test_checkout_lf_y_conversion_crlf_cargan_mismo_artefacto(self):
        manifest=json.loads((self.folder/'manifest.json').read_text())['archivos']
        for name in ('metadatos_candidato.json','preflight.json'):
            b=(self.folder/name).read_bytes()
            self.assertNotIn(b'\r\n',b)
            self.assertNotEqual(hashlib.sha256(b).hexdigest(),manifest[name])
            self.assertTrue(service._hash_json_o_bytes(b,manifest[name],name))
        for eol in (b'\n',b'\r\n'):
            with self.subTest(eol=eol):
                for name in ('metadatos_candidato.json','preflight.json'):
                    b=(self.folder/name).read_bytes().replace(b'\r\n',b'\n')
                    (self.folder/name).write_bytes(b.replace(b'\n',eol))
                with patch.object(service,'DIRECTORY',self.folder):
                    p=service._profile()
                    self.assertEqual(type(p['model']).__name__,'GradientBoostingClassifier')
                    self.assertEqual(p['metadata']['ultima_referencia_usada'],'2026-09-08')
                    self.assertTrue(service.informacion_candidato()['disponible'])
                self.assertEqual(hashlib.sha256((self.folder/service.ARTIFACT).read_bytes()).hexdigest(),manifest[service.ARTIFACT])

    def test_cambios_de_valores_y_espacios_siguen_rechazados(self):
        for name in ('metadatos_candidato.json','preflight.json'):
            path=self.folder/name;original=path.read_bytes()
            for cambio in (original.replace(b'2026-10-21',b'2026-10-22'),original+b' '):
                self.assertNotEqual(cambio,original)
                path.write_bytes(cambio)
                with patch.object(service,'DIRECTORY',self.folder):
                    with self.assertRaises(service.CandidatoNoDisponible):service._profile()
                path.write_bytes(original)

    def test_binarios_codigo_calendario_y_json_no_autorizado_estrictos(self):
        payload=b'{\r\n"valor":1\r\n}'
        sha=hashlib.sha256(payload).hexdigest()
        for name in ('otro.json','inicio_evaluacion.json','constructor_congelado.py','calendario_aplicado.csv',service.ARTIFACT):
            self.assertFalse(service._hash_json_o_bytes(payload.replace(b'\r\n',b'\n'),sha,name))
        for name in (service.ARTIFACT,'constructor_congelado.py','calendario_aplicado.csv'):
            path=self.folder/name;original=path.read_bytes();path.write_bytes(original+b'\n')
            with patch.object(service,'DIRECTORY',self.folder):
                with self.assertRaises(service.CandidatoNoDisponible):service._profile()
            path.write_bytes(original)
