FROM python:3.9-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY bot_telegram.py .

# Command to run the application
CMD ["python", "bot_telegram.py"]
