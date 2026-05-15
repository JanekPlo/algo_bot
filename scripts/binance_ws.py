# src/exchange/binance_ws.py
import json, threading, time
from websocket import WebSocketApp  # pip install websocket-client

WS_URL = "wss://stream.binance.com:9443/stream"

class BinanceWS:
    def __init__(self, symbols, stream="kline_1m", on_msg=None):
        self.params = [f"{s.lower()}@{stream}" for s in symbols]
        self.on_msg = on_msg
        self.ws = None
        self._stop = False

    def _on_open(self, ws):
        payload = {"method": "SUBSCRIBE", "params": self.params, "id": 1}
        ws.send(json.dumps(payload))

    def _on_message(self, ws, message):
        if self.on_msg:
            self.on_msg(json.loads(message))

    def _on_error(self, ws, err): print("[WS ERROR]", err)
    def _on_close(self, *a): print("[WS CLOSED]")

    def start(self):
        def run():
            while not self._stop:
                try:
                    self.ws = WebSocketApp(
                        WS_URL,
                        on_open=self._on_open,
                        on_message=self._on_message,
                        on_error=self._on_error,
                        on_close=self._on_close,
                    )
                    self.ws.run_forever(ping_interval=15, ping_timeout=10)
                except Exception as e:
                    print("[WS RECONNECT]", e)
                time.sleep(2)  # backoff
        t = threading.Thread(target=run, daemon=True)
        t.start()

    def stop(self):
        self._stop = True
        try:
            self.ws.close()
        except: pass
