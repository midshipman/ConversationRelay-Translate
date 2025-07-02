# VoxRay Translate - Real-Time Voice Translation System

A real-time voice translation system built with FastAPI, Twilio, and OpenAI that enables seamless bidirectional voice conversations between speakers of different languages.

## Overview

**VoxRay Translate** is a sophisticated real-time translation system that bridges language barriers by providing instant voice-to-voice translation during phone calls. The system automatically creates translation sessions, manages WebSocket connections, and uses OpenAI's GPT-4 for high-quality translations.

## Architecture

### Core Components

1. **Translation Session Management**
   - `TranslationSession` class manages call pairs and WebSocket connections
   - Tracks source and target call SIDs, phone numbers, and languages
   - Maintains WebSocket connections for both callers

2. **WebSocket Endpoints**
   - `/ws/source/{session_id}` - Handles source  caller websocket
   - `/ws/target/{session_id}` - Handles target  callee websocket
   - Real-time bidirectional voice processing

3. **Voice Webhooks**
   - `/voice/source/{session_id}` - Outbound call handler for source language speaker
   - `/voice/target/{session_id}` - Outbound call handler for target language speaker

4. **Translation Engine**
   - Streaming translation using OpenAI GPT-4
   - Configurable source and target languages
   - Real-time token-by-token translation delivery

## How It Works

### Translation Process

```
Source Caller → WebSocket → Translation Engine → WebSocket → Target Callee
     ↑                                                            ↓
     ←───────── WebSocket ← Translation Engine ←────────      WebSocket
```

## Features

- **Bidirectional Translation**: Both parties can speak and hear in own language
- **Real-time Translation**: Instant translation with minimal delay
- **Session Management**: Robust session tracking with automatic cleanup
- **Configurable Languages**: Environment-based language configuration
- **Error Handling**: Comprehensive error handling and logging
- **Scalable Architecture**: FastAPI-based async architecture

## Configuration

### Environment Variables

Create a `.env` file with the following variables:

```env
# Twilio Configuration
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_PHONE_NUMBER=your_twilio_phone_number

# Target Configuration
TARGET_PHONE_NUMBER=target_phone_number

# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key

# Language Configuration (Optional)
SOURCE_LANGUAGE=en-US
TARGET_LANGUAGE=de-DE
```

### Supported Languages

- **Source Language**: Defaults to `en-US` (English)
- **Target Language**: Defaults to `de-DE` (German)
- Configurable via environment variables for any language pair


## Installation and Setup

1. **Install Dependencies**:
   ```bash
   uv sync
   ```

2. **Configure Environment**: Create `.env` file with required variables

3. **Run the Application**:
   ```bash
   uv run  main.py
   ```

4. **Configure Twilio Webhook**: Point your Twilio phone number webhook to `/voice`


## Current Status

**Phase 1**: ✅ Complete - Bidirectional real-time translation
- Source-to-target translation
- Target-to-source translation
- Session management
- WebSocket handling
- Environment-based configuration

## Future Enhancements

- **Multi-language Support**: Support for more than two languages per session
- **Conference Calls**: Multiple participants with different languages
- **Recording and Transcription**: Call recording with translated transcripts
- **Web Interface**: Browser-based translation interface
- **Mobile App**: Native mobile application




        