import logging
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

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

        for attempt in range(max_retries + 1):
            kwargs["headers"] = self.auth_headers()
            resp = requests.request(method, url, **kwargs)
            if resp.status_code != 401:
                break
            if attempt < max_retries:
                logger.warning("Token expired or invalid. Attempting refresh...")
                self.login()
                time.sleep(RETRY_DELAY)

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

    def fetch_doc(self, doc_id: str) -> dict:
        resp = self.request(
            "GET",
            f"/api/communications/{doc_id}",
            params={"depth": 1}
        )
        return resp.json()

    def update_status(self, doc_id: str, status: str):
        self.request(
            "PATCH",
            f"/api/communications/{doc_id}",
            json={"status": status}
        )