# """
# websocket_server.py
# Recall.ai WebSocket server — clean non-streaming pipeline with interrupt support.

# Architecture:
#   Recall.ai WebSocket → event router → transcript buffer / speech_off trigger
#                       → _process() → Trigger + LLM parallel → TTS → Recall inject

# NOISE MIXING: DISABLED — saves 300-500ms
#   To re-enable: uncomment the noise block in _process() (clearly marked below)
# """

# import asyncio
# import json
# import time
# import base64
# from aiohttp import web
# import aiohttp
# from collections import deque

# from Trigger import TriggerDetector
# from Agent import PMAgent
# from Speaker import CartesiaSpeaker, _mix_noise


# def ts():
#     return time.strftime("%H:%M:%S")

# def elapsed(since: float) -> str:
#     return f"{(time.time() - since)*1000:.0f}ms"

# WORDS_PER_SECOND = 3.2


# class WebSocketServer:
#     def __init__(self, port: int = 8000, bot_id: str = None):
#         self.port             = port
#         self.trigger          = TriggerDetector()
#         self.agent            = PMAgent()
#         self.speaker          = CartesiaSpeaker(bot_id=bot_id)
#         self._speaking        = False
#         self._buffer          = []
#         self._buffer_task     = None
#         self._convo_history   = deque(maxlen=8)
#         self._current_speaker: str | None  = None
#         self._speech_start_at: float       = 0.0
#         # ── Interrupt support ─────────────────────────────────────────────────
#         self._current_task:    asyncio.Task | None = None
#         self._interrupt_event: asyncio.Event       = asyncio.Event()
#         # ─────────────────────────────────────────────────────────────────────
#         self.app = web.Application()
#         self.app.router.add_get("/ws",     self.handle_websocket)
#         self.app.router.add_get("/health", self.handle_health)

#     async def handle_health(self, request: web.Request) -> web.Response:
#         return web.json_response({"status": "ok", "speaking": self._speaking})

#     async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
#         ws = web.WebSocketResponse(heartbeat=30)
#         await ws.prepare(request)
#         print(f"[{ts()}] ✅ Recall.ai WebSocket connected")
#         try:
#             async for msg in ws:
#                 if msg.type == aiohttp.WSMsgType.TEXT:
#                     await self._handle_event(msg.data)
#                 elif msg.type == aiohttp.WSMsgType.ERROR:
#                     print(f"[{ts()}] ⚠️  WebSocket error: {ws.exception()}")
#                 elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
#                     print(f"[{ts()}] WebSocket closed")
#                     break
#         except Exception as e:
#             print(f"[{ts()}] WebSocket handler error: {e}")
#         finally:
#             print(f"[{ts()}] WebSocket disconnected")
#         return ws

#     async def _handle_event(self, raw: str):
#         t = time.time()
#         try:
#             payload = json.loads(raw)
#         except Exception:
#             return

#         event = payload.get("event", "")

#         if event == "transcript.data":
#             inner   = payload.get("data", {}).get("data", {})
#             words   = inner.get("words", [])
#             text    = " ".join(w.get("text", "") for w in words).strip()
#             speaker = inner.get("participant", {}).get("name", "Unknown")
#             if not text:
#                 return
#             print(f"\n[{ts()}] [{speaker}] {text}  ⏱ {elapsed(t)}")
#             self._buffer.append((speaker, text, t))
#             if self._buffer_task and not self._buffer_task.done():
#                 self._buffer_task.cancel()
#             self._buffer_task = asyncio.create_task(
#                 self._flush_after_silence(speaker, t)
#             )

#         elif event == "participant_events.speech_off":
#             speaker = (
#                 payload.get("data", {}).get("data", {})
#                        .get("participant", {}).get("name", "Unknown")
#             )
#             print(f"[{ts()}] 🔇 {speaker} stopped speaking")
#             if self._buffer_task and not self._buffer_task.done():
#                 self._buffer_task.cancel()
#             if self._buffer:
#                 full_text = " ".join(txt for _, txt, _ in self._buffer)
#                 t0        = self._buffer[0][2]
#                 self._buffer.clear()
#                 self._convo_history.append(f"{speaker}: {full_text}")
#                 print(f"[{ts()}] 📝 speech_off flush: \"{full_text}\"")
#                 task = asyncio.create_task(self._process(full_text, speaker, t0))
#                 self._current_task = task

#         elif event == "participant_events.speech_on":
#             speaker = (
#                 payload.get("data", {}).get("data", {})
#                        .get("participant", {}).get("name", "Unknown")
#             )
#             self._current_speaker = speaker
#             self._speech_start_at = t
#             print(f"[{ts()}] 🎤 {speaker} started speaking")
#             if self._speaking:
#                 print(f"[{ts()}] ⚡ INTERRUPT — {speaker} cut in")
#                 self._interrupt_event.set()

#         elif event == "participant_events.join":
#             name = (
#                 payload.get("data", {}).get("data", {})
#                        .get("participant", {}).get("name", "Unknown")
#             )
#             if name and name.lower() != "sam":
#                 print(f"[{ts()}] 👋 {name} joined")
#                 asyncio.create_task(self._greet_participant(name, t))

#         elif event == "participant_events.leave":
#             name = (
#                 payload.get("data", {}).get("data", {})
#                        .get("participant", {}).get("name", "Unknown")
#             )
#             if name and name.lower() != "sam":
#                 print(f"[{ts()}] 👋 {name} left")

#     async def _greet_participant(self, name: str, t0: float):
#         await asyncio.sleep(2.0)
#         if self._speaking:
#             return
#         greeting = f"Hey {name}, welcome to the call!"
#         self._convo_history.append(f"Sam: {greeting}")
#         await self._speak_response(greeting, t0)

#     async def _flush_after_silence(self, speaker: str, t0: float):
#         try:
#             await asyncio.sleep(0.8)
#         except asyncio.CancelledError:
#             return
#         if not self._buffer:
#             return
#         full_text = " ".join(txt for _, txt, _ in self._buffer)
#         self._buffer.clear()
#         self._convo_history.append(f"{speaker}: {full_text}")
#         print(f"[{ts()}] 📝 silence flush: \"{full_text}\"")
#         task = asyncio.create_task(self._process(full_text, speaker, t0))
#         self._current_task = task

#     async def _process(self, text: str, speaker: str, t0: float):
#         if self._speaking:
#             print(f"[{ts()}] ⚠️  Sam is speaking — dropping")
#             return

#         self._speaking = True
#         try:
#             context         = "\n".join(self._convo_history)
#             memory_snapshot = [m[0] for m in self.agent.memory[-20:]]

#             t1 = time.time()
#             print(f"[{ts()}] Trigger + LLM in parallel...")

#             trigger_task = asyncio.create_task(
#                 self.trigger.should_respond(text, speaker, context, memory_snapshot)
#             )
#             llm_task = asyncio.create_task(
#                 self.agent.respond_with_context(text, context)
#             )

#             should = await trigger_task
#             print(f"[{ts()}] Trigger: {'YES' if should else 'NO'} ({elapsed(t1)})")

#             if not should:
#                 llm_task.cancel()
#                 return

#             response = await llm_task
#             print(f"[{ts()}] LLM {elapsed(t1)}: \"{response}\"")
#             print(f"[{ts()}] TTS...")

#             t2 = time.time()
#             try:
#                 voice_bytes = await asyncio.wait_for(
#                     self.speaker._synthesise(response), timeout=10.0
#                 )
#             except Exception as e:
#                 print(f"[{ts()}] ⚠️  TTS error: {e}")
#                 return

#             tts_ms     = (time.time() - t2) * 1000
#             loop       = asyncio.get_event_loop()
#             word_count = len(response.split())

#             # ── NOISE MIXING DISABLED — saves 300-500ms ───────────────────────
#             # To re-enable noise mixing:
#             #   1. Uncomment the block below
#             #   2. Comment out the 2 lines after it (audio_bytes / audio_duration_ms)
#             #
#             # if self.speaker._noise_slices and word_count > 5:
#             #     try:
#             #         result = await loop.run_in_executor(
#             #             None, _mix_noise,
#             #             voice_bytes, self.speaker._noise_slices, response
#             #         )
#             #         if isinstance(result, tuple):
#             #             audio_bytes, audio_duration_ms = result
#             #         else:
#             #             audio_bytes       = result
#             #             audio_duration_ms = word_count * 1000 // 3
#             #     except Exception as e:
#             #         print(f"[{ts()}] ⚠️  Noise error: {e}")
#             #         audio_bytes       = voice_bytes
#             #         audio_duration_ms = word_count * 1000 // 3
#             # else:
#             #     audio_bytes       = voice_bytes
#             #     audio_duration_ms = word_count * 1000 // 3
#             # ─────────────────────────────────────────────────────────────────
#             audio_bytes       = voice_bytes
#             audio_duration_ms = word_count * 1000 // 3

#             b64 = await loop.run_in_executor(
#                 None,
#                 lambda ab=audio_bytes: base64.b64encode(ab).decode("utf-8")
#             )
#             t3 = time.time()
#             await self.speaker._inject_into_meeting(b64)
#             inject_ms = (time.time() - t3) * 1000

#             print(f"[{ts()}] TTS {tts_ms:.0f}ms | Inject {inject_ms:.0f}ms | Lock {audio_duration_ms/1000:.1f}s | TOTAL {elapsed(t0)}")

#             # ── Interruptible lock ────────────────────────────────────────────
#             already_elapsed = (time.time() - t2) * 1000
#             wait_ms         = max(100, audio_duration_ms - already_elapsed)
#             self._interrupt_event.clear()
#             try:
#                 await asyncio.wait_for(
#                     self._interrupt_event.wait(),
#                     timeout=wait_ms / 1000
#                 )
#                 print(f"[{ts()}] ⚡ Sam interrupted — releasing lock early")
#                 self._convo_history.append(f"Sam: {response} [interrupted]")
#                 self.trigger.mark_responded()
#                 return
#             except asyncio.TimeoutError:
#                 pass
#             # ─────────────────────────────────────────────────────────────────

#             self._convo_history.append(f"Sam: {response}")
#             self.trigger.mark_responded()
#             print(f"[{ts()}] ✅ Done")

#         except Exception as e:
#             print(f"[{ts()}] ❌ _process error: {e}")
#         finally:
#             self._speaking = False

#     async def _speak_response(self, text: str, t0: float):
#         if self._speaking:
#             return
#         self._speaking = True
#         try:
#             loop        = asyncio.get_event_loop()
#             voice_bytes = await self.speaker._synthesise(text)
#             b64 = await loop.run_in_executor(
#                 None, lambda: base64.b64encode(voice_bytes).decode("utf-8")
#             )
#             await self.speaker._inject_into_meeting(b64)
#             word_count = len(text.split())
#             self._interrupt_event.clear()
#             try:
#                 await asyncio.wait_for(
#                     self._interrupt_event.wait(),
#                     timeout=word_count / WORDS_PER_SECOND
#                 )
#             except asyncio.TimeoutError:
#                 pass
#         except Exception as e:
#             print(f"[{ts()}] ⚠️  _speak_response error: {e}")
#         finally:
#             self._speaking = False

#     async def start(self):
#         runner = web.AppRunner(self.app)
#         await runner.setup()
#         site = web.TCPSite(runner, "0.0.0.0", self.port)
#         await site.start()
#         print(f"[{ts()}] WebSocket server ready on ws://0.0.0.0:{self.port}/ws")
#         print(f"[{ts()}] Health check: http://localhost:{self.port}/health\n")


"""
websocket_server.py — OPTIMIZED + AVATAR SUPPORT

Pipeline:
  Transcript → Trigger + LLM stream (parallel) → Parallel TTS → Concat MP3 → Send to avatar page
  Avatar page plays audio (Recall captures it) + sends to Simli for lip sync

  Falls back to Recall output_audio API if avatar page isn't connected.
"""

import asyncio
import json
import time
import base64
import os
from aiohttp import web
import aiohttp
from collections import deque

from Trigger import TriggerDetector
from Agent import PMAgent
from Speaker import CartesiaSpeaker, _mix_noise


def ts():
    return time.strftime("%H:%M:%S")

def elapsed(since: float) -> str:
    return f"{(time.time() - since)*1000:.0f}ms"

WORDS_PER_SECOND = 3.2
_SENTINEL = object()


class WebSocketServer:
    def __init__(self, port: int = 8000, bot_id: str = None):
        self.port             = port
        self.trigger          = TriggerDetector()
        self.agent            = PMAgent()
        self.speaker          = CartesiaSpeaker(bot_id=bot_id)
        self._speaking        = False
        self._audio_playing   = False
        self._convo_history   = deque(maxlen=8)

        # Current processing state
        self._current_task:       asyncio.Task | None = None
        self._current_text:       str   = ""
        self._current_speaker:    str   = ""
        self._interrupt_event:    asyncio.Event = asyncio.Event()

        self._generation:   int   = 0

        self._buffer:       list  = []
        self._buffer_task:  asyncio.Task | None = None

        # ── Avatar WebSocket connection ───────────────────────────────────────
        self._avatar_ws: aiohttp.web.WebSocketResponse | None = None

        self.app = web.Application()
        self.app.router.add_get("/ws",        self.handle_websocket)
        self.app.router.add_get("/health",    self.handle_health)
        self.app.router.add_get("/avatar",    self.handle_avatar_page)
        self.app.router.add_get("/avatar-ws", self.handle_avatar_ws)

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "speaking": self._speaking,
            "avatar_connected": self._avatar_ws is not None,
        })

    # ── Avatar page — served to Recall's headless browser ─────────────────────
    async def handle_avatar_page(self, request: web.Request) -> web.Response:
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "avatar.html")
        try:
            with open(html_path, "r") as f:
                html = f.read()
            return web.Response(text=html, content_type="text/html")
        except FileNotFoundError:
            return web.Response(text="avatar.html not found", status=404)

    # ── Avatar WebSocket — avatar page connects here to receive audio ─────────
    async def handle_avatar_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._avatar_ws = ws
        print(f"[{ts()}] 🎭 Avatar page connected")

        # Send config (Simli keys if available)
        config = {
            "type": "config",
            "simli_api_key": os.environ.get("SIMLI_API_KEY", ""),
            "simli_face_id": os.environ.get("SIMLI_FACE_ID", ""),
        }
        await ws.send_str(json.dumps(config))

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("type") == "ready":
                        print(f"[{ts()}] 🎭 Avatar ready")
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        except Exception as e:
            print(f"[{ts()}] 🎭 Avatar WS error: {e}")
        finally:
            self._avatar_ws = None
            print(f"[{ts()}] 🎭 Avatar page disconnected")
        return ws

    # ── Send audio to avatar page or fallback to Recall API ───────────────────
    async def _deliver_audio(self, audio_bytes: bytes):
        """Send audio to avatar page if connected, else inject via Recall API."""
        if self._avatar_ws is not None and not self._avatar_ws.closed:
            try:
                await self._avatar_ws.send_bytes(audio_bytes)
                print(f"[Speaker] Audio sent to avatar page ({len(audio_bytes)} bytes)")
                return
            except Exception as e:
                print(f"[{ts()}] ⚠️  Avatar send failed: {e}, falling back to Recall API")

        # Fallback: old Recall output_audio injection
        b64 = base64.b64encode(audio_bytes).decode("utf-8")
        await self.speaker._inject_into_meeting(b64)

    async def _stop_avatar_audio(self):
        """Tell avatar page to stop playing audio."""
        if self._avatar_ws is not None and not self._avatar_ws.closed:
            try:
                await self._avatar_ws.send_str(json.dumps({"type": "stop"}))
            except Exception:
                pass
        # Also try Recall API stop (works for both modes)
        await self.speaker.stop_audio()

    # ── Recall.ai WebSocket handler (unchanged) ──────────────────────────────
    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        print(f"[{ts()}] ✅ Recall.ai WebSocket connected")
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_event(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"[{ts()}] ⚠️  WS error: {ws.exception()}")
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                    break
        except Exception as e:
            print(f"[{ts()}] WS handler error: {e}")
        finally:
            print(f"[{ts()}] WebSocket disconnected")
        return ws

    async def _handle_event(self, raw: str):
        t = time.time()
        try:
            payload = json.loads(raw)
        except Exception:
            return

        event = payload.get("event", "")

        if event == "transcript.data":
            inner   = payload.get("data", {}).get("data", {})
            words   = inner.get("words", [])
            text    = " ".join(w.get("text", "") for w in words).strip()
            speaker = inner.get("participant", {}).get("name", "Unknown")
            if not text or speaker.lower() == "sam":
                return

            print(f"\n[{ts()}] [{speaker}] {text}  ⏱ {elapsed(t)}")

            if self._buffer_task and not self._buffer_task.done():
                self._buffer_task.cancel()

            if (self._speaking and self._current_speaker == speaker):
                combined = f"{self._current_text} {text}".strip()
                print(f"[{ts()}] 🔄 Combined: \"{combined}\" — restarting")
                if self._current_task and not self._current_task.done():
                    self._current_task.cancel()
                if self._audio_playing:
                    asyncio.create_task(self._stop_avatar_audio())
                self._speaking = False
                self._audio_playing = False
                self._interrupt_event.set()
                await asyncio.sleep(0)
                self._start_process(combined, speaker, t)

            elif self._speaking and self._current_speaker != speaker:
                print(f"[{ts()}] ⚡ INTERRUPT — {speaker} cut in")
                asyncio.create_task(self._stop_avatar_audio())
                self._interrupt_event.set()
                self._start_process(text, speaker, t)

            else:
                self._start_process(text, speaker, t)

        elif event == "participant_events.speech_off":
            speaker = (
                payload.get("data", {}).get("data", {})
                       .get("participant", {}).get("name", "Unknown")
            )
            print(f"[{ts()}] 🔇 {speaker} stopped speaking")
            if self._buffer and not self._speaking:
                full_text = " ".join(txt for _, txt, _ in self._buffer)
                t0        = self._buffer[0][2]
                self._buffer.clear()
                self._start_process(full_text, speaker, t0)
            self._buffer.clear()

        elif event == "participant_events.speech_on":
            speaker = (
                payload.get("data", {}).get("data", {})
                       .get("participant", {}).get("name", "Unknown")
            )
            print(f"[{ts()}] 🎤 {speaker} started speaking")
            if self._speaking and self._current_speaker != speaker:
                print(f"[{ts()}] ⚡ INTERRUPT (speech_on) — {speaker} cut in")
                asyncio.create_task(self._stop_avatar_audio())
                self._interrupt_event.set()

        elif event == "participant_events.join":
            name = (
                payload.get("data", {}).get("data", {})
                       .get("participant", {}).get("name", "Unknown")
            )
            if name and name.lower() != "sam":
                print(f"[{ts()}] 👋 {name} joined")
                asyncio.create_task(self._greet_participant(name, t))

        elif event == "participant_events.leave":
            name = (
                payload.get("data", {}).get("data", {})
                       .get("participant", {}).get("name", "Unknown")
            )
            if name and name.lower() != "sam":
                print(f"[{ts()}] 👋 {name} left")

    def _start_process(self, text: str, speaker: str, t0: float):
        self._generation     += 1
        my_gen                = self._generation
        self._current_text    = text
        self._current_speaker = speaker
        self._interrupt_event.clear()
        task = asyncio.create_task(self._process(text, speaker, t0, my_gen))
        self._current_task = task

    async def _greet_participant(self, name: str, t0: float):
        await asyncio.sleep(2.0)
        if self._speaking:
            return
        greeting = f"Hey {name}, welcome to the call!"
        self._convo_history.append(f"Sam: {greeting}")
        await self._speak_response(greeting, t0)

    # ══════════════════════════════════════════════════════════════════════════
    # _process — streaming LLM + parallel TTS + avatar delivery
    # ══════════════════════════════════════════════════════════════════════════

    async def _process(self, text: str, speaker: str, t0: float, generation: int = 0):
        if self._speaking:
            print(f"[{ts()}] ⚠️  Already speaking — dropping")
            return

        self._speaking = True
        self._interrupt_event.clear()
        my_generation = generation

        try:
            context         = "\n".join(self._convo_history)
            memory_snapshot = [m[0] for m in self.agent.memory[-20:]]

            t1 = time.time()

            # ── Trigger + LLM stream in true parallel ─────────────────────────
            sentence_queue = asyncio.Queue()
            llm_task = asyncio.create_task(
                self.agent.stream_sentences_to_queue(text, context, sentence_queue)
            )
            trigger_task = asyncio.create_task(
                self.trigger.should_respond(text, speaker, context, memory_snapshot)
            )

            print(f"[{ts()}] Trigger + LLM streaming in parallel...")

            should = await trigger_task
            print(f"[{ts()}] Trigger: {'YES' if should else 'NO'} ({elapsed(t1)})")

            if not should:
                llm_task.cancel()
                return

            # ── Collect sentences + fire TTS as each arrives ──────────────────
            sentences = []
            tts_tasks = []

            while True:
                if self._interrupt_event.is_set() or my_generation != self._generation:
                    print(f"[{ts()}] ⚡ Superseded — aborting")
                    llm_task.cancel()
                    for t_task in tts_tasks:
                        t_task.cancel()
                    return

                try:
                    item = await asyncio.wait_for(sentence_queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    print(f"[{ts()}] ⚠️  LLM queue timeout")
                    break

                if item is None:
                    break

                sentences.append(item)
                idx = len(sentences)
                print(f"[{ts()}] LLM sentence {idx} ({elapsed(t1)}): \"{item}\"")
                tts_tasks.append(asyncio.create_task(self.speaker._synthesise(item)))

            if not sentences or not tts_tasks:
                return

            # ── Await all TTS ─────────────────────────────────────────────────
            t2 = time.time()
            print(f"[{ts()}] TTS ({len(sentences)} sentences, parallel+streamed)...")
            results = await asyncio.gather(*tts_tasks, return_exceptions=True)

            audio_chunks = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"[{ts()}] ⚠️  TTS sentence {i+1} failed: {result}")
                else:
                    audio_chunks.append(result)

            if not audio_chunks:
                return

            if self._interrupt_event.is_set() or my_generation != self._generation:
                print(f"[{ts()}] ⚡ Superseded during TTS")
                return

            tts_ms = (time.time() - t2) * 1000

            # ── Concat + deliver ──────────────────────────────────────────────
            audio_bytes = b"".join(audio_chunks)
            response = " ".join(sentences)
            word_count = len(response.split())
            audio_duration_ms = word_count * 1000 // 3

            if self._interrupt_event.is_set() or my_generation != self._generation:
                return

            t3 = time.time()
            await self._deliver_audio(audio_bytes)
            self._audio_playing = True
            inject_ms = (time.time() - t3) * 1000

            print(f"[{ts()}] TTS {tts_ms:.0f}ms | Deliver {inject_ms:.0f}ms | Lock {audio_duration_ms/1000:.1f}s | TOTAL {elapsed(t0)}")

            # ── Interruptible lock ────────────────────────────────────────────
            already_elapsed = (time.time() - t2) * 1000
            wait_ms = max(100, audio_duration_ms - already_elapsed)
            try:
                await asyncio.wait_for(
                    self._interrupt_event.wait(),
                    timeout=wait_ms / 1000
                )
                print(f"[{ts()}] ⚡ Sam interrupted — lock released")
                self._convo_history.append(f"Sam: {response} [interrupted]")
                self.trigger.mark_responded()
                return
            except asyncio.TimeoutError:
                pass

            self._audio_playing = False
            self._convo_history.append(f"Sam: {response}")
            self.trigger.mark_responded()
            print(f"[{ts()}] ✅ Done")

        except asyncio.CancelledError:
            print(f"[{ts()}] 🔄 Task cancelled (new text combined)")
        except Exception as e:
            import traceback
            print(f"[{ts()}] ❌ _process error: {e}")
            traceback.print_exc()
        finally:
            self._audio_playing = False
            self._speaking      = False

    async def _speak_response(self, text: str, t0: float):
        print(f"[{ts()}] _speak_response called: speaking={self._speaking} text='{text[:40]}'")
        if self._speaking:
            print(f"[{ts()}] _speak_response: already speaking — skipping")
            return
        self._speaking = True
        try:
            voice_bytes = await self.speaker._synthesise(text)
            print(f"[{ts()}] _speak_response: TTS done — {len(voice_bytes)} bytes")
            await self._deliver_audio(voice_bytes)
            word_count = len(text.split())
            self._interrupt_event.clear()
            try:
                await asyncio.wait_for(
                    self._interrupt_event.wait(),
                    timeout=word_count / WORDS_PER_SECOND
                )
            except asyncio.TimeoutError:
                pass
            print(f"[{ts()}] _speak_response: done")
        except Exception as e:
            import traceback
            print(f"[{ts()}] ⚠️  _speak_response error: {e}")
            traceback.print_exc()
        finally:
            self._speaking = False

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()
        print(f"[{ts()}] WebSocket server ready on ws://0.0.0.0:{self.port}/ws")
        print(f"[{ts()}] Avatar page: http://localhost:{self.port}/avatar")
        print(f"[{ts()}] Health check: http://localhost:{self.port}/health\n")
