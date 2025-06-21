from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import Response
from typing import Dict, List, Optional
import json
import os
from openai import AsyncOpenAI
from dotenv import load_dotenv
import uvicorn

# Load environment variables
load_dotenv()
app = FastAPI()
sessions: Dict[str, List[dict]] = {}
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def draft_response(messages):
    stream = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        stream=True,
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



@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    call_sid: Optional[str] = None
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            # print("Message type: ", message["type"])
            print("ConvRelay Message: ", message)

            if message["type"] == "setup":
                call_sid = message["callSid"]
                print(f"Setup initiated for call from **{message['from'][-2:]}")
                print(f"Call SID: {call_sid}")

                # Initialize conversation history and system prompt
                sessions[str(call_sid)] = [
                    {"role": "system", "content": "You are a helpful translation assistant, translating from English to German."}
                ]

            elif message["type"] == "prompt":
                prompt = message["voicePrompt"]
                print("Prompt: ", prompt)

                # Retrieve and update conversation history
                # Make sure call_sid is not None
                conversation = sessions[str(call_sid)] if call_sid is not None else []
                conversation.append({"role": "user", "content": prompt})

                # print(conversation)

                response = ""
                async for event in draft_response(messages=conversation):
                    await websocket.send_json(event)
                    response += event["token"]

                #save back
                conversation.append({"role": "assistant", "content": response})
                sessions[str(call_sid)] = conversation

            elif message["type"] == "interrupt":
                print("Response interrupted")

            elif message["type"] == "error":
                print("Error")

            else:
                print("Unknown message type")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if call_sid:
            sessions.pop(call_sid, None)
        print("Client has disconnected.")

@app.post("/incoming")
@app.post("/voice")
async def voice_webhook(request: Request):
    """Handle incoming Twilio voice calls and connect to ConversationRelay"""
    # Twilio sends URL-encoded form data, not JSON
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    from_number = form_data.get("From")
    to_number = form_data.get("To")
    call_status = form_data.get("CallStatus")
    
    print(f"Incoming call from {from_number} to {to_number} with SID: {call_sid}, Status: {call_status}")
    
    # Get the host from request headers, handle both HTTP and HTTPS
    host = request.headers.get('host')
    ws_url = f"wss://{host}/ws"
    print(f"Websocket URL: {ws_url}")
    
    # TwiML response for ConversationRelay
    twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Connect>
                    <ConversationRelay url="{ws_url}" />
                </Connect>
            </Response>'''
    
    return Response(content=twiml, media_type="text/xml")

if __name__ == "__main__":
    
    uvicorn.run(app, host="0.0.0.0", port=8080)