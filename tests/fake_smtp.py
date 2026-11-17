"""A tiny SMTP server for tests: accepts every message and keeps it in `messages`."""
import email
import socketserver
import threading
from email import policy


class FakeSMTP:
    def __init__(self):
        self.messages: list[email.message.EmailMessage] = []
        self.fail = False  # answer 554 to DATA, to test failures
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def reply(self, line):
                self.wfile.write(line.encode() + b"\r\n")

            def handle(self):
                self.reply("220 fake ESMTP")
                while line := self.rfile.readline():
                    cmd = line.decode(errors="replace").strip().upper()
                    if cmd.startswith(("EHLO", "HELO")):
                        self.reply("250 fake")
                    elif cmd.startswith(("MAIL", "RCPT", "RSET", "NOOP")):
                        self.reply("250 ok")
                    elif cmd == "DATA":
                        self.reply("354 go ahead")
                        data = b""
                        while (chunk := self.rfile.readline()) not in (b".\r\n", b""):
                            data += chunk[1:] if chunk.startswith(b"..") else chunk
                        if outer.fail:
                            self.reply("554 rejected")
                        else:
                            outer.messages.append(email.message_from_bytes(data, policy=policy.default))
                            self.reply("250 queued")
                    elif cmd == "QUIT":
                        self.reply("221 bye")
                        return
                    else:
                        self.reply("502 not implemented")

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def to(self) -> list[str]:
        return [m["To"] for m in self.messages]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
