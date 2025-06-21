import json
import os
import uvicorn
from typing import Dict, List, Optional
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import Response
from openai import AsyncOpenAI
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()
app = FastAPI()
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

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
        self.source_language = "en-US"  # Default source language
        self.target_language = "zh-CN"  # Default target language

# Session storage
translation_sessions: Dict[str, TranslationSession] = {}

async def translate_text_streaming(text: str, source_lang: str = "en-US", target_lang: str = "de-DE"):
    """Streaming translation function using OpenAI"""
    messages = [
        {"role": "system", "content": f"You are a professional real-time translator. Translate the following {source_lang} text to {target_lang}. Provide only the translation, no explanations or additional text."},
        {"role": "user", "content": text}
    ]
    
    stream = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        stream=True,
        temperature=0.3,  # Lower temperature for more consistent translations
    )
    
    async for chunk in stream:
        if chunk.choices[0].delta.content is not None:
            yield {
                "token": chunk.choices[0].delta.content,
                "last": False,
                "type": "text",
            }
    
    yield {
        "token": "",
        "type": "text",
        "last": True,
    }

@app.websocket("/ws/source/{session_id}")
async def source_websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for source language callers"""
    await websocket.accept()
    call_sid: Optional[str] = None
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            print(f"Source WebSocket Message: {message}")

            if message["type"] == "setup":
                call_sid = message["callSid"]
                print(f"Source setup initiated for call SID: {call_sid}")
                
                # Update session with source WebSocket
                if session_id in translation_sessions:
                    session = translation_sessions[session_id]
                    session.source_websocket = websocket
                    

            elif message["type"] == "prompt":
                prompt = message["voicePrompt"]
                print(f"Source prompt: {prompt}")
                
                # Get session for language configuration
                if session_id in translation_sessions:
                    session = translation_sessions[session_id]
                    source_lang = session.source_language
                    target_lang = session.target_language
                    
                    # Translate using streaming
                    translated_text = ""
                    async for event in translate_text_streaming(prompt, source_lang, target_lang):
                        await session.target_websocket.send_json(event)
                        translated_text += event["token"]
                    
                    print(f"Translated from {source_lang} to {target_lang}: {translated_text}")
                    
                   

            elif message["type"] == "interrupt":
                print("Source response interrupted")

            elif message["type"] == "error":
                print("Source WebSocket error")

    except Exception as e:
        print(f"Source WebSocket error: {e}")
    finally:
        if session_id in translation_sessions:
            translation_sessions.pop(session_id, None)
        print("Source client disconnected.")

@app.websocket("/ws/target/{session_id}")
async def target_websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for target language callers"""
    await websocket.accept()
    call_sid: Optional[str] = None
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            print(f"Target WebSocket Message: {message}")

            if message["type"] == "setup":
                call_sid = message["callSid"]
                print(f"Target ws setup initiated for call SID: {call_sid}")
                
                # Update session with target WebSocket
                if session_id in translation_sessions:
                    session = translation_sessions[session_id]
                    session.target_call_sid = call_sid
                    session.target_websocket = websocket

            elif message["type"] == "prompt":
                # Phase 2: Translate target back to source
                prompt = message["voicePrompt"]
                print(f"Target prompt: {prompt}")
                
                if session_id in translation_sessions:
                    session = translation_sessions[session_id]
                    target_lang = session.target_language
                    source_lang = session.source_language
                    
                    # Translate target → source
                    translated_text = ""
                    async for event in translate_text_streaming(prompt, target_lang, source_lang):
                        await session.source_websocket.send_json(event)
                        translated_text += event["token"]
                    
                    print(f"Translated from {target_lang} to {source_lang}: {translated_text}")
                    
            elif message["type"] == "interrupt":
                print("Target response interrupted")

            elif message["type"] == "error":
                print("Target WebSocket error")

    except Exception as e:
        print(f"Target WebSocket error: {e}")
    finally:
        print("Target client disconnected.")

@app.post("/incoming")
@app.post("/voice")
async def voice_webhook(request: Request):
    """Handle incoming source language calls"""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    from_number = form_data.get("From")
    to_number = form_data.get("To")
    call_status = form_data.get("CallStatus")
    
    print(f"Incoming source call from {from_number} to {to_number} with SID: {call_sid}, Status: {call_status}")
    
    # Get target phone number from environment
    target_number = os.getenv("TARGET_PHONE_NUMBER") or os.getenv("GERMAN_PHONE_NUMBER")
    twilio_number = os.getenv("TWILIO_PHONE_NUMBER")
    
    if not target_number or not twilio_number:
        print("Missing target or Twilio phone numbers in environment")
        return Response(content="Error: Missing phone configuration", status_code=500)
    
    # Create unique session ID
    session_id = f"session_{call_sid}"
    
    # Create translation session immediately with all phone number info
    session = TranslationSession(session_id, call_sid)
    session.source_phone_number = from_number
    session.target_phone_number = target_number  # Initialize target number here
    session.source_language = os.getenv("SOURCE_LANGUAGE", "en-US")
    session.target_language = os.getenv("TARGET_LANGUAGE", "de-DE")
    translation_sessions[session_id] = session
    
    print(f"Created translation session: {session_id}")
    
    # Get the host from request headers and pass it to the outbound call
    host = request.headers.get('host')
    await create_outbound_target_call(session_id, host, target_number, twilio_number)
    
    # Get the host from request headers
    ws_url = f"wss://{host}/ws/source/{session_id}"
    print(f"Source WebSocket URL: {ws_url}")
    
    # TwiML response for ConversationRelay with source language settings
    twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Connect>
                    <ConversationRelay url="{ws_url}" language="{session.source_language}" />
                </Connect>
            </Response>'''
    
    return Response(content=twiml, media_type="text/xml")

async def create_outbound_target_call(session_id: str, host: str, target_number: str, twilio_number: str):
    """Create outbound call to target language speaker"""
    try:
        # Get existing session and update it
        if session_id not in translation_sessions:
            print(f"Session {session_id} not found!")
            return
            
        session = translation_sessions[session_id]
        
        # Use the host passed from the incoming request
        webhook_url = f"https://{host}/voice/target/{session_id}"
        print(f"Webhook URL for target caller: {webhook_url}")
        call = twilio_client.calls.create(
            to=target_number,
            from_=twilio_number,
            url=webhook_url,
            method="POST"
        )
        
        # Update session with target call info
        session.target_call_sid = call.sid
        print(f"Created outbound call to target number: {call.sid}")
        
    except Exception as e:
        print(f"Error creating outbound call: {e}")

@app.post("/voice/target/{session_id}")
async def target_voice_webhook(request: Request, session_id: str):
    """Handle outbound target language calls"""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    from_number = form_data.get("From")
    to_number = form_data.get("To")
    call_status = form_data.get("CallStatus")
    
    print(f"Outbound target call from {from_number} to {to_number} with SID: {call_sid}, Status: {call_status}")
    
    # Get the host from request headers
    host = request.headers.get('host')
    ws_url = f"wss://{host}/ws/target/{session_id}"
    print(f"Target WebSocket URL: {ws_url}")
    
    # Get target language from session or default to German
    if session_id in translation_sessions:
        target_language = translation_sessions[session_id].target_language
    
    # TwiML response for ConversationRelay with target language settings
    twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Connect>
                    <ConversationRelay url="{ws_url}" language="{target_language}" />
                </Connect>
            </Response>'''
    
    return Response(content=twiml, media_type="text/xml")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)