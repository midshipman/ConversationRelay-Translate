import os
import json
import uvicorn
import logging
from typing import Dict, Optional
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import Response
from openai import AsyncOpenAI
from dotenv import load_dotenv
from twilio.rest import Client
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
import time
from litellm import acompletion
import litellm

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logging.getLogger('twilio').setLevel(logging.WARNING)

load_dotenv()
app = FastAPI()
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
music_url = "https://pub-09065925c50a4711a49096e7dbee29ce.r2.dev/arcade-melody-295434.mp3"
wait_url = "https://pub-09065925c50a4711a49096e7dbee29ce.r2.dev/mixkit-marimba-ringtone-1359.wav"

os.environ['CONFIDENT_API_KEY']=os.getenv("CONFIDENT_API_KEY")
litellm.success_callback = ["deepeval"]
litellm.failure_callback = ["deepeval"]

# Session management for translation pairs
# Translation session management
class TranslationSession:
    def __init__(self, session_id: str, source_call_sid: str):
        self.session_id = session_id
        self.source_call_sid = source_call_sid  # Incoming call SID
        self.target_call_sid: Optional[str] = None  # Outbound call SID
        self.source_websocket: Optional[WebSocket] = None  # Incoming caller's WebSocket
        self.target_websocket: Optional[WebSocket] = None  # Outbound caller's WebSocket
        self.source_phone_number = None  # Incoming caller's phone
        self.target_phone_number = None  # Outbound caller's phone
        self.source_language = ""  # Default source language
        self.target_language = ""  # Default target language
        self.source_tts_provider = "ElevenLabs"  # Default source TTS provider
        self.source_voice = ""  # Default source voice
        self.target_tts_provider = "ElevenLabs"  # Default target TTS provider
        self.target_voice = ""  # Default target voice
        self.host = None  # Request host for WebSocket URLs

# Session storage
translation_sessions: Dict[str, TranslationSession] = {}

async def translate_text_streaming(text: str, source_lang: str = "en-US", target_lang: str = "de-DE"):
    """Streaming translation function using OpenAI"""
    messages = [
        {"role": "system", "content": f"You are a professional real-time translator. Translate the following {source_lang} text to {target_lang}. Provide only the translation, no explanations or additional text."},
        {"role": "user", "content": text}
    ]
    # logging.info(f"Translating text: {text} from {source_lang} to {target_lang}")
    stream = await acompletion(
        model="gpt-4.1-nano",
        messages=messages,
        stream=True,
        temperature=0.3,  # Lower temperature for more consistent translations
    )

    async for chunk in stream:
        if chunk.choices[0].delta.content is not None:
            token = chunk.choices[0].delta.content
            # logging.info(f"Received token from llm: {token}")
            yield {
                "token": token,
                "last": False,
                "type": "text",
            }

    yield {
        "token": "",
        "type": "text",
        "last": True,
    }

def generate_conversation_relay_twiml(ws_url: str, language: str, tts_provider: str, voice: str = "") -> str:
    """Generate TwiML response for ConversationRelay with language and TTS settings"""
    voice_attr = f' voice="{voice}"' if voice else ''
    # Set transcription provider based on language
    if language.startswith('ar-'):
        stt_attr = f' transcriptionProvider="google"'
    else:
        stt_attr = f' transcriptionProvider="deepgram"'

    twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Connect>
                    <ConversationRelay
                        debug="speaker-events"
                        url="{ws_url}"
                        language="{language}"
                        ttsProvider="{tts_provider}"
                        {voice_attr}
                        {stt_attr}/>
                </Connect>
            </Response>'''
    return twiml

async def check_session_readiness_and_notify(session: TranslationSession, session_id: str) -> bool:
    """Check if session is ready and send appropriate notifications to users.

    Returns:
        bool: True if session is ready for translation, False if still waiting
    """
    if not (session.source_websocket and session.target_websocket):
        logging.info(f"Source or target websocket not ready for session {session_id}")

        wait_event = {
            "type": "play",
            "source": wait_url,
            "loop": 0,
            "preemptible": True,
            "interruptible": False
        }

        # Send waiting message in appropriate language to each participant
        if session.source_websocket:
            await session.source_websocket.send_json(wait_event)

        if session.target_websocket:
            await session.source_websocket.send_json(wait_event)

        return False
    else:
        # Send ready message in appropriate language to each participant
        # Translate ready message to source language
        source_ready_text = ""
        async for event in translate_text_streaming(
            "You are ready to talk.",
            "en-US",
            session.source_language
        ):
            source_ready_text += event["token"]

        ready_message_source = {
            "type": "text",
            "token": source_ready_text,
            "last": True,
        }
        await session.source_websocket.send_json(ready_message_source)

        # Translate ready message to target language
        target_ready_text = ""
        async for event in translate_text_streaming(
            "You are ready to talk.",
            "en-US",
            session.target_language
        ):
            target_ready_text += event["token"]

        ready_message_target = {
            "type": "text",
            "token": target_ready_text,
            "last": True,
        }
        await session.target_websocket.send_json(ready_message_target)

        return True

async def cleanup_session(session_id: str):
    
    if session_id not in translation_sessions:
        return
    
    session = translation_sessions[session_id]
    end_message = {
        "type": "end",
        "handoffData": "clean up session"
    }
    
    if session.source_websocket:
        try:
            await session.source_websocket.send_json(end_message)
            await session.source_websocket.close()
        except Exception as e:
            logging.error(f"Error closing source WebSocket: {e}")
        finally:
            session.source_websocket = None
    
    if session.target_websocket:
        try:
            await session.target_websocket.send_json(end_message)
            await session.target_websocket.close()
        except Exception as e:
            logging.error(f"Error closing target WebSocket: {e}")
        finally:
            session.target_websocket = None
    
    translation_sessions.pop(session_id, None)
    logging.info(f"Translation session {session_id} removed")

async def create_outbound_target_call(session_id: str, host: str, target_number: str, twilio_number: str):
    """Create outbound call to target language speaker"""
    try:
        # Get existing session and update it
        if session_id not in translation_sessions:
            logging.error(f"Session {session_id} not found!")
            return

        session = translation_sessions[session_id]

        # Use the host passed from the incoming request
        webhook_url = f"https://{host}/voice/target/{session_id}"
        logging.info(f"Webhook URL for target caller: {webhook_url}")
        call = twilio_client.calls.create(
            to=target_number,
            from_=twilio_number,
            url=webhook_url,
            method="POST",
            record=True
        )

        # Update session with target call info
        session.target_call_sid = call.sid
        logging.info(f"Created outbound call to target number: {call.sid}")

    except Exception as e:
        logging.error(f"Error creating outbound call: {e}")

async def create_outbound_source_call(session_id: str, host: str, source_number: str, twilio_number: str):
    """Create outbound call to source language speaker"""
    try:
        # Get existing session and update it
        if session_id not in translation_sessions:
            logging.error(f"Session {session_id} not found!")
            return

        session = translation_sessions[session_id]

        # Use the host passed from the incoming request
        webhook_url = f"https://{host}/voice/source/{session_id}"
        logging.info(f"Webhook URL for source caller: {webhook_url}")
        call = twilio_client.calls.create(
            to=source_number,
            from_=twilio_number,
            url=webhook_url,
            method="POST",
            record=True
        )

        # Update session with source call info
        session.source_call_sid = call.sid
        logging.info(f"Created outbound call to source number: {call.sid}")

    except Exception as e:
        logging.error(f"Error creating outbound source call: {e}")

@app.websocket("/ws/source/{session_id}")
async def source_websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for source language callers"""
    await websocket.accept()
    call_sid: Optional[str] = None

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            logging.info(f"Source WebSocket Message: {message}")

            if message["type"] == "setup":
                call_sid = message["callSid"]
                logging.info(f"Source setup initiated for call SID: {call_sid}")

                # Update session with source WebSocket
                if session_id in translation_sessions:
                    session = translation_sessions[session_id]
                    session.source_websocket = websocket

                    if not await check_session_readiness_and_notify(session, session_id):
                        continue  # Skip this prompt if not ready

            if message["type"] == "prompt":
                prompt = message["voicePrompt"]
                logging.info(f"Source prompt: {prompt}")

                # Get session for language configuration
                if session_id in translation_sessions:
                    session = translation_sessions[session_id]
                    source_lang = session.source_language
                    target_lang = session.target_language

                    if not session.target_websocket:
                        continue  # Skip this prompt if not ready

                    # Translate using streaming
                    translated_text = ""
                    async for event in translate_text_streaming(prompt, source_lang, target_lang):
                        await session.target_websocket.send_json(event)
                        translated_text += event["token"]

                    logging.info(f"Translated from {source_lang} to {target_lang}: {translated_text}")

                    #play music while waiting for the response
                    music_event = {
                            "type": "play",
                            "source": music_url,
                            "loop": 0,
                            "preemptible": True,
                            "interruptible": True
                        }
                    await session.source_websocket.send_json(music_event)

            if message["type"] == "info":
                logging.info(f"Source info: {message}")

            if message["type"] == "interrupt":
                logging.info("Source interrupted")

            if message["type"] == "error":
                logging.error("Source WebSocket error")

    except Exception as e:
        logging.error(f"Source WebSocket error: {e}")
    finally:
        await cleanup_session(session_id)
        logging.info("Source client disconnected.")

@app.websocket("/ws/target/{session_id}")
async def target_websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for target language callers"""
    await websocket.accept()
    call_sid: Optional[str] = None

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            # logging.info(f"Target WebSocket Message: {message}")

            if message["type"] == "setup":
                call_sid = message["callSid"]
                logging.info(f"Target ws setup initiated for call SID: {call_sid}")

                # Update session with target WebSocket
                if session_id in translation_sessions:
                    session = translation_sessions[session_id]
                    session.target_call_sid = call_sid
                    session.target_websocket = websocket

                    if not await check_session_readiness_and_notify(session, session_id):
                        continue  # Skip this prompt if not ready

            if message["type"] == "prompt":
                # Phase 2: Translate target back to source
                prompt = message["voicePrompt"]
                logging.info(f"Target prompt: {prompt}")

                if session_id in translation_sessions:
                    session = translation_sessions[session_id]
                    target_lang = session.target_language
                    source_lang = session.source_language

                    if not session.source_websocket:
                        continue  # Skip this prompt if not ready

                    # Translate target → source
                    translated_text = ""
                    async for event in translate_text_streaming(prompt, target_lang, source_lang):
                        await session.source_websocket.send_json(event)
                        translated_text += event["token"]

                    logging.info(f"Translated from {target_lang} to {source_lang}: {translated_text}")

                    #play music while waiting for the response
                    music_event = {
                            "type": "play",
                            "source": music_url,
                            "loop": 0,
                            "preemptible": True,
                            "interruptible": True
                        }
                    await session.target_websocket.send_json(music_event)

            if message["type"] == "info":
                logging.info(f"Target info: {message}")

            if message["type"] == "interrupt":
                logging.warning("Target interrupted")

            if message["type"] == "error":
                logging.error("Target WebSocket error")

    except Exception as e:
        logging.error(f"Target WebSocket error: {e}")
    finally:
        await cleanup_session(session_id)
        logging.info("Target client disconnected.")


@app.post("/voice/target/{session_id}")
async def target_voice_webhook(request: Request, session_id: str):
    """Handle outbound target language calls"""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    from_number = form_data.get("From")
    to_number = form_data.get("To")
    call_status = form_data.get("CallStatus")

    logging.info(f"Outbound target call from {from_number} to {to_number} with SID: {call_sid}, Status: {call_status}")

    # Get the host from request headers
    host = request.headers.get('host')
    ws_url = f"wss://{host}/ws/target/{session_id}"
    logging.info(f"Target WebSocket URL: {ws_url}")

    # Get target language and TTS settings from session or defaults
    target_language = ""
    target_tts_provider = ""
    target_voice = ""
    if session_id in translation_sessions:
        session = translation_sessions[session_id]
        target_language = session.target_language
        target_tts_provider = session.target_tts_provider
        target_voice = session.target_voice

   # Generate TwiML response using the new function
    twiml = generate_conversation_relay_twiml(
        ws_url=ws_url,
        language=target_language,
        tts_provider=target_tts_provider,
        voice=target_voice
    )

    return Response(content=twiml, media_type="text/xml")

@app.post("/voice/source/{session_id}")
async def source_voice_webhook(request: Request, session_id: str):
    """Handle outbound source language calls"""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    from_number = form_data.get("From")
    to_number = form_data.get("To")
    call_status = form_data.get("CallStatus")

    logging.info(f"Outbound source call from {from_number} to {to_number} with SID: {call_sid}, Status: {call_status}")

    # Get the host from request headers
    host = request.headers.get('host')
    ws_url = f"wss://{host}/ws/source/{session_id}"
    logging.info(f"Source WebSocket URL: {ws_url}")

    # Get source language and TTS settings from session or defaults
    source_language = ""  # default
    source_tts_provider = ""  # default
    source_voice = ""  # default
    if session_id in translation_sessions:
        session = translation_sessions[session_id]
        source_language = session.source_language
        source_tts_provider = session.source_tts_provider
        source_voice = session.source_voice

     # Generate TwiML response using the new function
    twiml = generate_conversation_relay_twiml(
        ws_url=ws_url,
        language=source_language,
        tts_provider=source_tts_provider,
        voice=source_voice
    )

    return Response(content=twiml, media_type="text/xml")

@app.get("/")
async def call_form():
    """Serve HTML form for initiating translation calls"""
    return FileResponse("start.html")

@app.post("/initiate-call")
async def initiate_call(request: Request):
    """Handle form submission to initiate translation calls"""
    form_data = await request.form()
    from_number = form_data.get("from_number")
    to_number = form_data.get("to_number")
    source_language = form_data.get("source_language")
    target_language = form_data.get("target_language")
    source_tts_provider = form_data.get("source_tts_provider", "ElevenLabs")
    source_voice = form_data.get("source_voice", "")
    target_tts_provider = form_data.get("target_tts_provider", "ElevenLabs")
    target_voice = form_data.get("target_voice", "")

    # Validate required fields
    if not all([from_number, to_number, source_language, target_language]):
        return HTMLResponse(
            content="<h1>Error: All fields are required</h1><a href='/'>Go back</a>",
            status_code=400
        )

    # Get Twilio phone number from environment
    twilio_number = os.getenv("TWILIO_PHONE_NUMBER")
    if not twilio_number:
        return HTMLResponse(
            content="<h1>Error: Twilio phone number not configured</h1><a href='/'>Go back</a>",
            status_code=500
        )

    try:
        # Create unique session ID
        session_id = f"session_{int(time.time())}_{from_number.replace('+', '')}_{to_number.replace('+', '')}"

        # Create translation session
        session = TranslationSession(session_id, "")
        session.source_phone_number = from_number
        session.target_phone_number = to_number
        session.source_language = source_language
        session.target_language = target_language
        session.source_tts_provider = source_tts_provider
        session.source_voice = source_voice
        session.target_tts_provider = target_tts_provider
        session.target_voice = target_voice
        session.host = request.headers.get('host')
        translation_sessions[session_id] = session

        logging.info(f"Created manual translation session: {session_id}")
        logging.info(f"From: {from_number} ({source_language}) -> To: {to_number} ({target_language})")
        logging.info(f"Source TTS: {source_tts_provider}/{source_voice}, Target TTS: {target_tts_provider}/{target_voice}")

        # Create outbound calls to both parties
        await create_outbound_source_call(session_id, session.host, from_number, twilio_number)
        await create_outbound_target_call(session_id, session.host, to_number, twilio_number)


        return JSONResponse(
            content={
                "status": "success",
                "message": "call started successfully",
                "session_id": session_id,
                "from_number": from_number,
                "to_number": to_number,
                "source_language": source_language,
                "target_language": target_language
            },
            status_code=200
        )

    except Exception as e:
        logging.error(f"Error initiating call: {e}")
        return HTMLResponse(
            content=f"<h1>Error: {str(e)}</h1><a href='/'>Go back</a>",
            status_code=500
        )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

