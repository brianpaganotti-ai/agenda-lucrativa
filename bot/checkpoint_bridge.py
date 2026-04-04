"""
checkpoint_bridge.py — Monitora o diretório _opensquad/checkpoints/
e envia notificações ao usuário no Telegram quando um novo checkpoint
é criado pelo OpenSquad.

Roda como thread dentro do telegram_bot.py (não é um processo separado).
Pode também ser executado standalone para debug:
  python bot/checkpoint_bridge.py
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

NEXUS_DIR = Path(os.getenv("NEXUS_DIR", "/opt/nexus"))
CHECKPOINTS_DIR = NEXUS_DIR / "_opensquad" / "checkpoints"
POLL_INTERVAL = 5  # segundos


class CheckpointBridge:
    """
    Monitora _opensquad/checkpoints/ por polling e chama callback
    quando encontra um novo checkpoint com status='pending'.

    Uso:
        bridge = CheckpointBridge(on_checkpoint=minha_funcao)
        bridge.start()   # inicia loop em thread separada
        bridge.stop()    # para o loop
    """

    def __init__(self, on_checkpoint: Callable[[dict], None]) -> None:
        self.on_checkpoint = on_checkpoint
        self._running = False
        self._seen: set[str] = set()

    def start(self) -> None:
        """Inicia o loop de polling em thread."""
        import threading
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("CheckpointBridge iniciado (poll a cada %ds)", POLL_INTERVAL)

    def stop(self) -> None:
        self._running = False
        logger.info("CheckpointBridge parado.")

    def _loop(self) -> None:
        CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
        while self._running:
            try:
                self._scan()
            except Exception as e:
                logger.error("Erro no scan de checkpoints: %s", e)
            time.sleep(POLL_INTERVAL)

    def _scan(self) -> None:
        for cp_file in sorted(CHECKPOINTS_DIR.glob("*.json")):
            key = str(cp_file)
            if key in self._seen:
                continue
            try:
                with open(cp_file) as f:
                    data = json.load(f)
                if data.get("status") == "pending":
                    self._seen.add(key)
                    data["_file"] = key
                    logger.info("Novo checkpoint detectado: %s", cp_file.name)
                    self.on_checkpoint(data)
            except json.JSONDecodeError:
                # Arquivo ainda sendo escrito — tentar no próximo ciclo
                pass
            except Exception as e:
                logger.error("Erro ao ler checkpoint %s: %s", cp_file, e)

    @staticmethod
    def resolve(cp_file: str, decision: str) -> None:
        """
        Escreve a decisão no arquivo de checkpoint para retomar o pipeline.
        decision: 'approved' | 'rejected'
        """
        path = Path(cp_file)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint não encontrado: {cp_file}")
        with open(path, "r+") as f:
            data = json.load(f)
            data["status"] = decision
            data["resolved_at"] = time.time()
            f.seek(0)
            json.dump(data, f, indent=2)
            f.truncate()
        logger.info("Checkpoint %s resolvido como: %s", path.name, decision)

    @staticmethod
    def create_example(squad: str = "teste", message: str = "Aprovação necessária") -> Path:
        """
        Cria um checkpoint de exemplo para debug.
        Uso: CheckpointBridge.create_example()
        """
        CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
        cp_id = f"cp_{int(time.time())}"
        cp_file = CHECKPOINTS_DIR / f"{cp_id}.json"
        data = {
            "id": cp_id,
            "squad": squad,
            "message": message,
            "status": "pending",
            "created_at": time.time(),
        }
        with open(cp_file, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Checkpoint de exemplo criado: %s", cp_file)
        return cp_file


# ---------------------------------------------------------------------------
# Standalone debug
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    def handler(cp: dict) -> None:
        print(f"\n[CHECKPOINT] Squad: {cp.get('squad')} | {cp.get('message')}")
        decision = input("Aprovar? (s/n): ").strip().lower()
        CheckpointBridge.resolve(cp["_file"], "approved" if decision == "s" else "rejected")

    bridge = CheckpointBridge(on_checkpoint=handler)
    bridge.start()

    print(f"Monitorando: {CHECKPOINTS_DIR}")
    print("Ctrl+C para sair\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        bridge.stop()
