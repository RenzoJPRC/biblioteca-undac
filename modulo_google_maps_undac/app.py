from flask import Flask, redirect, url_for
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from routes.api_external import api_external_bp

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "undac_maps_secret_key_2026")

# Registrar Blueprint de Geolocalización y Google Maps
app.register_blueprint(api_external_bp)

@app.route('/')
def index():
    return redirect(url_for('api_external.mapa_filiales_page'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print("=" * 65)
    print("  🗺️  MÓDULO REST API: GOOGLE MAPS & GEOLOCALIZACIÓN UNDAC")
    print("=" * 65)
    print(f"  • Servidor activo en el puerto: {port}")
    print(f"  • Vista Principal: http://127.0.0.1:{port}/mapa")
    print(f"  • Endpoint REST Ubicaciones: http://127.0.0.1:{port}/api/ubicaciones_undac")
    print(f"  • Endpoint REST Geocodificación: http://127.0.0.1:{port}/api/geocodificar?q=Tarma")
    print(f"  • Endpoint REST Clima por Coordenada: http://127.0.0.1:{port}/api/clima_coordenada?lat=-10.668115&lng=-76.253753")
    print("=" * 65)
    app.run(host='0.0.0.0', port=port, debug=False)
