import logging
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

RETRY_DELAY = float(os.getenv("RETRY_DELAY_SECONDS", 2))


class MzingaClient:
    def __init__(self, url, email, password):
        self.url = url
        self.email = email
        self.password = password
        self.token = None

    def request(self, method, endpoint, max_retries: int = 1, **kwargs):
        """Wrapper to handle retry in case of 401"""
        url = f"{self.url}{endpoint}"

        resp = requests.request(method, url, **kwargs)

        for _ in range(max_retries):
            if resp.status_code != 401:
                break
            logger.warning("Token expired or invalid. Attempting refresh...")
            self.login()
            time.sleep(RETRY_DELAY)
            resp = requests.request(method, url, **kwargs)

        resp.raise_for_status()
        return resp
    
    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}
    
    def login(self) -> str:
        resp = requests.post(
            f"{self.url}/api/users/login",
            json={"email": self.email, "password": self.password},
        )
        resp.raise_for_status()
        self.token = resp.json()["token"]
        logger.info("Authenticated with Mzinga API")
        return self.token

    def fetch_pending(self) -> list:
        resp = self.request(
            "GET",
            "/api/communications",
            params={"where[status][equals]": "pending", "depth": 1},
            headers=self.auth_headers(),
        )
        return resp.json().get("docs", [])

    def update_status(self, doc_id: str, status: str):
        self.request(
            "PATCH",
            f"/api/communications/{doc_id}",
            json={"status": status},
            headers=self.auth_headers(),
        )