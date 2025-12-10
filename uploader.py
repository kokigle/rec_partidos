import requests
import os

# CONFIGURACION STREAMTAPE
API_LOGIN = "c63384b752fc0304b170"
API_KEY = "6BeDd4Wbayu9yMo"

def subir_video(ruta_archivo):
    print(f"☁️  Subiendo {os.path.basename(ruta_archivo)} a Streamtape...")
    
    if not os.path.exists(ruta_archivo):
        print("❌ Archivo no encontrado.")
        return None

    try:
        # 1. Obtener URL de subida
        url_api = f"https://api.streamtape.com/file/ul?login={API_LOGIN}&key={API_KEY}"
        resp = requests.get(url_api).json()
        
        if resp['status'] != 200:
            print(f"❌ Error API: {resp.get('msg')}")
            return None
            
        upload_link = resp['result']['url']
        
        # 2. Subir
        with open(ruta_archivo, 'rb') as f:
            files = {'file1': f}
            resp_subida = requests.post(upload_link, files=files).json()
            
        if resp_subida['status'] == 200:
            link_final = resp_subida['result']['url']
            print(f"✅ SUBIDA EXITOSA: {link_final}")
            return link_final
        else:
            print("❌ Falló la subida.")
            
    except Exception as e:
        print(f"❌ Error crítico: {e}")
        return None