# Data Processing Pipeline
Pipeline that converts emails in Mbox format and whatsapp chat backups into a json structure, to use it in a RAG system later. The conversations are uploaded to openwebui and into an MongoDb in the last step.

Used models:
- Mistral: voxtral-mini-latest (audio transcription)
- Mistral: mistral-medium-latest (analyze images)

## Setup

1. **Environment Setup**
   Update conda environment with: `mamba env update -n dev -f environment.yml`

2. **Environment Variables**
   Copy `.env.example` to `.env` and fill in your actual API keys and configuration values:
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env` with your actual values:
   - `MISTRAL_API_KEY_TRANSCRIBE`: Mistral AI API key for audio transcription
   - `MISTRAL_API_KEY_VISION`: Mistral AI API key for image analysis
   - `MONGODB_URI`: MongoDB connection string
   - `OPENWEBUI_API_KEY`: OpenWebUI authentication token
   - `OPENWEBUI_KNOWLEDGE_ID_ATTACHMENTS`: Knowledge base ID for attachments
   - `OPENWEBUI_KNOWLEDGE_ID_CONVERSATIONS`: Knowledge base ID for conversations

3. **Install Python Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

## Data Format

{
    "Id": "850999fd-f65f-4205-9053-61a0c1d32370",
    "channel": "Mail" | "Whatsapp",
    "StartDate": "2025-07-06 08:52",
    "EndDate": "2025-07-06 08:52",
    "Sender": "Raik@aa" | "Raik",
    "Receiver"?: "only for mails",
    "Messages": [
        {
            "Id": 1,
            "Text"?: "bla",
            "Timestamp": "2024-07-19 10:55",
            "Subject"?:"mail subject",
            "Attachments":[
                {
                    "id":1,
                    "path":"{Id}_{Message_Id}_{AttachmentCount}.png",
                    "extension":".png",
                    "mimetype":"image/png",
                    "original_filename": "adasde.png"
                },
                {
                    "id":2,
                    "path":"{Id}_{Message_Id}_{AttachmentCount}.pdf"
                    "extension":".pdf",
                    "mimetype":"application/pdf",
                    "original_filename": "abcdef.pdf"
                }
            ]
        }
    ]
}
