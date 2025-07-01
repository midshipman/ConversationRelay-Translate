import json
import os
import uvicorn
from typing import Dict, List, Optional
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import Response
from openai import AsyncOpenAI
from dotenv import load_dotenv
from twilio.rest import Client
from fastapi.responses import HTMLResponse, FileResponse
import time

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

async def create_outbound_source_call(session_id: str, host: str, source_number: str, twilio_number: str):
    """Create outbound call to source language speaker"""
    try:
        # Get existing session and update it
        if session_id not in translation_sessions:
            print(f"Session {session_id} not found!")
            return
            
        session = translation_sessions[session_id]
        
        # Use the host passed from the incoming request
        webhook_url = f"https://{host}/voice/source/{session_id}"
        print(f"Webhook URL for source caller: {webhook_url}")
        call = twilio_client.calls.create(
            to=source_number,
            from_=twilio_number,
            url=webhook_url,
            method="POST"
        )
        
        # Update session with source call info
        session.source_call_sid = call.sid
        print(f"Created outbound call to source number: {call.sid}")
        
    except Exception as e:
        print(f"Error creating outbound source call: {e}")

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

@app.post("/voice/source/{session_id}")
async def source_voice_webhook(request: Request, session_id: str):
    """Handle outbound source language calls"""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    from_number = form_data.get("From")
    to_number = form_data.get("To")
    call_status = form_data.get("CallStatus")
    
    print(f"Outbound source call from {from_number} to {to_number} with SID: {call_sid}, Status: {call_status}")
    
    # Get the host from request headers
    host = request.headers.get('host')
    ws_url = f"wss://{host}/ws/source/{session_id}"
    print(f"Source WebSocket URL: {ws_url}")
    
    # Get source language from session or default to English
    source_language = "en-US"  # default
    if session_id in translation_sessions:
        source_language = translation_sessions[session_id].source_language
    
    # TwiML response for ConversationRelay with source language settings
    twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Connect>
                    <ConversationRelay url="{ws_url}" language="{source_language}" />
                </Connect>
            </Response>'''
    
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
        session.host = request.headers.get('host')
        translation_sessions[session_id] = session
        
        print(f"Created manual translation session: {session_id}")
        print(f"From: {from_number} ({source_language}) -> To: {to_number} ({target_language})")
        
        # Create outbound calls to both parties
        await create_outbound_source_call(session_id, session.host, from_number, twilio_number)
        await create_outbound_target_call(session_id, session.host, to_number, twilio_number)
        
        # Append log entry to start.html
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Read current start.html content
        with open("/Users/hwang/Desktop/Dev/voxray-translate/start.html", "r") as f:
            html_content = f.read()
        
        # Create log entry as simple list item
        log_entry = f"<li>{timestamp} - {from_number} ({source_language}) → {to_number} ({target_language})</li>\n"
        
        # Insert log entry into the call list
        call_list_pos = html_content.find('<ul id="call-list">')
        if call_list_pos != -1:
            # Find the position after the opening <ul> tag
            insert_pos = html_content.find('>', call_list_pos) + 1
            # Insert the new log entry at the beginning of the list
            html_content = html_content[:insert_pos] + '\n' + log_entry + html_content[insert_pos:]
        
        # Write updated content back to start.html
        with open("/Users/hwang/Desktop/Dev/voxray-translate/start.html", "w") as f:
            f.write(html_content)
        
        # Redirect back to the form page
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)
        
    except Exception as e:
        print(f"Error initiating call: {e}")
        return HTMLResponse(
            content=f"<h1>Error: {str(e)}</h1><a href='/'>Go back</a>",
            status_code=500
        )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)