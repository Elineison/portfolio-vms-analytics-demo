from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from app.env import load_env_file
from app.schemas import Event


load_env_file()

class EvidenceMailer:
    def __init__(self, outbox_dir: Path) -> None:
        self.outbox_dir = outbox_dir
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.host = os.getenv("VMS_SMTP_HOST", "")
        self.port = int(os.getenv("VMS_SMTP_PORT", "587"))
        self.username = os.getenv("VMS_SMTP_USER", "")
        self.password = os.getenv("VMS_SMTP_PASSWORD", "")
        self.sender = os.getenv("VMS_SMTP_FROM", self.username or "vms-demo@localhost")
        self.use_tls = os.getenv("VMS_SMTP_TLS", "1") != "0"

    def send_event(
        self,
        event: Event,
        recipient: str,
        snapshot_path: Path | None,
        face_paths: list[Path] | None = None,
    ) -> str:
        msg = EmailMessage()
        msg["Subject"] = f"[AVM Demo] {event.title}"
        msg["From"] = self.sender
        msg["To"] = recipient
        msg.set_content(
            "\n".join([
                event.title,
                "",
                event.message,
                f"Camera: {event.camera_name}",
                f"Pessoas na ROI: {event.people_count}",
                f"Horario UTC: {event.started_at}",
                f"Recortes de pessoas: {len(event.face_snapshot_files)}",
            ])
        )
        if snapshot_path and snapshot_path.exists():
            msg.add_attachment(
                snapshot_path.read_bytes(),
                maintype="image",
                subtype="jpeg",
                filename=snapshot_path.name,
            )
        for face_path in face_paths or []:
            if not face_path.exists():
                continue
            msg.add_attachment(
                face_path.read_bytes(),
                maintype="image",
                subtype="jpeg",
                filename=face_path.name,
            )

        if not self.host:
            outbox_path = self.outbox_dir / f"{event.id}.eml"
            outbox_path.write_bytes(bytes(msg))
            return "outbox_saved"

        with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
            if self.use_tls:
                smtp.starttls()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(msg)
        return "sent"
