"""Carga de los bytes versionados (LF) frente al manifiesto original de Windows."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ml.services import retorno_h1_experimental as service


class TestCargaH1Checkout(unittest.TestCase):
    def test_normalizacion_limitada_a_tres_json_autorizados(self):
        expected=hashlib.sha256(b'{\r\n"a":1\r\n}').hexdigest()
        for name in ['metadatos_candidato.json','preflight.json','inicio_evaluacion.json']:
            self.assertTrue(service._hash_json_o_bytes(b'{\n"a":1\n}',expected,name))
        for name in ['otro.json','receta.py','calendario_aplicado.csv','modelo.joblib']:
            self.assertFalse(service._hash_json_o_bytes(b'{\n"a":1\n}',expected,name))

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        source=service.DIRECTORY.relative_to(service.ROOT).as_posix()
        for name in ['manifest_evaluacion.json','metadatos_candidato.json','preflight.json','receta.py',
                     'calendario_aplicado.csv','inicio_evaluacion.json','modelo_retorno_h1_ventanas_experimental.joblib']:
            content=subprocess.check_output(['git','show',f'HEAD:{source}/{name}'],cwd=service.ROOT)
            (self.folder/name).write_bytes(content)

    def test_checkout_publicado_carga_modelo_y_habilita_opcion(self):
        manifest=json.loads((self.folder/'manifest_evaluacion.json').read_text(encoding='utf-8'))['archivos']
        payload=(self.folder/'metadatos_candidato.json').read_bytes()
        self.assertNotEqual(hashlib.sha256(payload).hexdigest(),manifest['metadatos_candidato.json'])
        with patch.object(service,'DIRECTORY',self.folder):
            loaded=service._profile()
            self.assertEqual(type(loaded['model']).__name__,'HistGradientBoostingClassifier')
            self.assertTrue(service.informacion_candidato()['disponible'])

    def test_valores_json_alterados_siguen_rechazados(self):
        path=self.folder/'metadatos_candidato.json'
        path.write_bytes(path.read_bytes().replace(b'2026-10-21',b'2026-10-22'))
        with patch.object(service,'DIRECTORY',self.folder):
            with self.assertRaises(service.CandidatoNoDisponible): service._profile()
            self.assertFalse(service.informacion_candidato()['disponible'])

    def test_espacios_distintos_de_eol_tambien_invalidan_json(self):
        path=self.folder/'metadatos_candidato.json'
        path.write_bytes(path.read_bytes()+b' ')
        with patch.object(service,'DIRECTORY',self.folder):
            with self.assertRaises(service.CandidatoNoDisponible): service._profile()

    def test_no_normaliza_ni_acepta_binarios_alterados(self):
        path=self.folder/'modelo_retorno_h1_ventanas_experimental.joblib'
        path.write_bytes(path.read_bytes()+b'\n')
        with patch.object(service,'DIRECTORY',self.folder):
            with self.assertRaises(service.CandidatoNoDisponible): service._profile()


if __name__=='__main__': unittest.main()
