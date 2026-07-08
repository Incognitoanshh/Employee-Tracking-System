import threading
import time
import requests

from client.application.managers.sync_manager import SyncManager


class SyncScheduler:

    @staticmethod
    def is_server_reachable():
        """Backend connectivity check karne ke liye helper method"""
        try:
            # Apne server ke status ya check endpoint par ek choti request maaro 2s timeout ke sath
            # Hum bina token ke bas ping check kar rahe hain connectivity check karne ke liye
            from client.core.config import API_BASE_URL
            requests.get(f"{API_BASE_URL.replace('/api', '')}", timeout=2)
            return True
        except Exception:
            return False

    @staticmethod
    def start():

        def run():
            consecutive_failures = 0

            while True:
                try:
                    # Gating Check: Agar server reachable hi nahi hai, toh फालतू requests mat fire karo
                    if not SyncScheduler.is_server_reachable():
                        consecutive_failures += 1
                        # Exponential backoff: Pehle failure par 60s, fir badhte-badhte max 5 min (300s) sleep karega
                        sleep_time = min(60 * consecutive_failures, 300)
                        print(f"[SYNC SCHEDULER] Server unreachable. Sleeping for {sleep_time}s to avoid log flooding...")
                        time.sleep(sleep_time)
                        continue
                    
                    # Server running hai, counters reset karo aur sync run karo
                    consecutive_failures = 0

                    try:
                        SyncManager.retry_uploads()
                    except Exception as error:
                        print("[SYNC SCHEDULER ERROR] uploads:", error)

                        try:
                            SyncManager.retry_logs()
                        except Exception as error:
                            print("[SYNC SCHEDULER ERROR] logs:", error)

                except Exception as loop_err:
                    print("[SYNC SCHEDULER CRITICAL] Loop exception:", loop_err)

                    time.sleep(60)

        thread = threading.Thread(
            target=run,
            daemon=True
        )
        thread.start()