FROM python:3.10-slim

# Обновляем систему и устанавливаем ffmpeg
RUN apt-get update && apt-get install -y ffmpeg

WORKDIR /app

# Копируем и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальной код
COPY . .

# Команда для запуска
CMD ["python", "main.py"]