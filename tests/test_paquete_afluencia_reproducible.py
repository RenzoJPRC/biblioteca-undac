"""Verifica candidato agregado existente y selectores sin BD ni entrenamiento."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import joblib
from sklearn.ensemble import GradientBoostingClassifier

from ml.services.predictor_afluencia import PredictorAfluencia


class TestPaqueteAfluencia(unittest.TestCase):
    def test_candidato_coincide_con_procedencia_y_solo_agregados(self):
        root=Path(__file__).resolve().parents[1]
        folder=root/'ml/models'
        pre=json.loads((root/'ml/experiments/retorno_h1_ventanas_finitas_20261021/preflight.json').read_text(encoding='utf-8'))
        old={k.replace('\\','/'):v for k,v in pre['modelos_antes'].items()}
        for name in ['modelo_afluencia_candidato_gb.joblib','metadatos_modelo_afluencia_candidato_gb.json']:
            path=folder/name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),old['ml/models/'+name])
        package=joblib.load(folder/'modelo_afluencia_candidato_gb.joblib')
        self.assertEqual(set(package),{'modelo','metadatos'})
        self.assertIsInstance(package['modelo'],GradientBoostingClassifier)
        self.assertEqual(list(package['modelo'].feature_names_in_),package['metadatos']['variables'])
        self.assertEqual(sorted(package['modelo'].classes_),['Alto','Bajo','Medio'])
        for key in ['X_train','y_train','AlumnoID','CodigoMatricula','DNI','NombreCompleto']:
            self.assertNotIn(key,package)

    def test_instalar_candidato_no_lo_convierte_en_predeterminado(self):
        with patch.dict(os.environ,{},clear=True):
            predictor=PredictorAfluencia()
        self.assertEqual(predictor.tipo_modelo_activo,'produccion')
        self.assertTrue(predictor.obtener_informacion()['modelo_candidato_disponible'])
        self.assertEqual(predictor.obtener_informacion('candidato')['algoritmo'],'GradientBoostingClassifier')
        self.assertEqual(predictor.obtener_informacion('produccion')['algoritmo'],'RandomForestClassifier')

    def test_hash_alterado_rechaza_antes_de_deserializar_candidato(self):
        root=Path(__file__).resolve().parents[1]
        original=Path.read_bytes
        path=root/'ml/models/modelo_afluencia_candidato_gb.joblib'
        def changed(file):
            content=original(file)
            return content+b'alterado' if file==path else content
        with patch.object(Path,'read_bytes',changed):
            predictor=PredictorAfluencia()
        info=predictor.obtener_informacion()
        self.assertFalse(info['modelo_candidato_disponible'])
        self.assertEqual(info['tipo_modelo'],'produccion')
        self.assertIn('integridad',info['motivo_candidato_no_disponible'])


if __name__=='__main__': unittest.main()
