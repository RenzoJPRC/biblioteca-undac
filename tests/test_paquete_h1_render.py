"""Verifica el paquete desplegable del candidato existente, sin BD ni entrenamiento."""
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

from ml.services import retorno_h1_experimental as candidate
from ml.services.analisis_individual import PredictorRetornoAlumno


class TestPaqueteH1Render(unittest.TestCase):
    def test_artefacto_y_fuentes_requeridas_estan_versionados_e_integros(self):
        root=candidate.ROOT
        names=['metadatos_candidato.json','preflight.json','receta.py','calendario_aplicado.csv',
               'inicio_evaluacion.json','modelo_retorno_h1_ventanas_experimental.joblib']
        manifest=json.loads((candidate.DIRECTORY/'manifest_evaluacion.json').read_text(encoding='utf-8'))['archivos']
        for name in names:
            path=candidate.DIRECTORY/name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),manifest[name])
            tracked=subprocess.check_output(['git','ls-files','--',path.relative_to(root).as_posix()],cwd=root,text=True)
            self.assertTrue(tracked.strip(),name)
        self.assertEqual(type(candidate._profile()['model']).__name__,'HistGradientBoostingClassifier')

    def test_runtime_coincide_con_dependencias_congeladas(self):
        requirements=(candidate.ROOT/'requirements.txt').read_text(encoding='utf-8').splitlines()
        frozen=(candidate.DIRECTORY/'requirements.txt').read_text(encoding='utf-8').splitlines()
        for dependency in frozen:
            if dependency.strip() and not dependency.startswith('#'):
                self.assertIn(dependency,requirements)
        self.assertIn('FROM python:3.11-slim-bookworm',(candidate.ROOT/'Dockerfile').read_text(encoding='utf-8'))

    def test_fecha_base_proviene_del_artefacto_y_no_del_corte_reciente(self):
        predictor=PredictorRetornoAlumno()
        self.assertEqual(predictor.fecha_creacion_artefacto,'2026-10-14')
        self.assertEqual(predictor.metadatos['periodo_disponible']['fecha_fin'],'2026-07-31')


if __name__=='__main__': unittest.main()
