from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8058
URL = f"http://127.0.0.1:{PORT}"
CHECK = URL + "/api/diagnose"

def box(text):
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)

def run(cmd):
    print("[RUN]", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, text=True)

def wait_server(timeout=60):
    last = ""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(CHECK, timeout=2) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = str(e)
            time.sleep(1)
    raise RuntimeError("서버 연결 확인 실패: " + last)

def main():
    os.chdir(ROOT)
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "data").mkdir(exist_ok=True)

    box("ScoutFlow V59-K12 Public Domain Ready")
    print("로컬 접속:", URL)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        lan_ip = sock.getsockname()[0]
        sock.close()
    except Exception:
        lan_ip = "내부IP확인필요"
    print("같은 공유기/와이파이 접속:", f"http://{lan_ip}:{PORT}")
    print("도메인 연결 후 예시:", f"http://scoutflow.kro.kr:{PORT}")
    print("외부 접속용으로 0.0.0.0 바인딩됩니다. 검은 창을 닫으면 서버도 꺼집니다.")

    req = ROOT / "requirements.txt"
    if req.exists():
        code = run([sys.executable, "-m", "pip", "install", "-r", str(req)]).returncode
        if code != 0:
            box("패키지 설치 실패")
            input("오류를 캡처한 뒤 Enter...")
            return

    box("서버 시작 중")
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=ROOT)
    try:
        diag = wait_server()
        box("Server connection OK")
        print(json.dumps(diag, ensure_ascii=False, indent=2))
        webbrowser.open(URL)
        print("\n사용 중에는 이 창을 닫지 마세요. 종료하려면 Ctrl+C를 누르세요.")
        while True:
            if proc.poll() is not None:
                box("서버가 종료되었습니다")
                print("Exit code:", proc.returncode)
                input("Enter...")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n서버 종료 요청")
    except Exception as e:
        box("서버 시작/연결 실패")
        print(e)
        if proc.poll() is not None:
            print("서버 종료 코드:", proc.returncode)
        print("logs/last_server_run.log도 확인하세요.")
        input("오류를 캡처한 뒤 Enter...")
    finally:
        try:
            proc.terminate()
        except Exception:
            pass

if __name__ == "__main__":
    main()
