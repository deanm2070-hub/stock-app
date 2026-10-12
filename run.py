"""啟動看盤網站：python run.py，然後用瀏覽器開 http://127.0.0.1:8000"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("stockapp.app:app", host="127.0.0.1", port=8000)
