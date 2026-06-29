import threading
import time

from client.application.managers.sync_manager import SyncManager


class SyncScheduler:

    @staticmethod
    def start():

        def run():
            while True:
                try:
                    SyncManager.retry_uploads()
                except Exception as error:
                    print("[SYNC SCHEDULER ERROR] uploads:", error)

                try:
                    SyncManager.retry_logs()
                except Exception as error:
                    print("[SYNC SCHEDULER ERROR] logs:", error)

                time.sleep(60)

        thread = threading.Thread(
            target=run,
            daemon=True
        )
        thread.start()
