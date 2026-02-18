#!/bin/bash
# Проверка: доступен ли API через ngrok.
# Запускайте когда бот уже запущен (python3 main.py) и ngrok уже запущен (ngrok http 8000).

URL="${1:-https://keaton-drys-gerda.ngrok-free.dev}"

echo "Проверка: $URL"
echo ""

echo "1. Локальный API (порт 8000):"
if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/health 2>/dev/null | grep -q 200; then
  echo "   OK — бот и FastAPI запущены."
else
  echo "   ОШИБКА — запустите бота: python3 main.py"
  exit 1
fi

echo ""
echo "2. Через ngrok (публичный URL):"
CODE=$(curl -s -o /tmp/ngrok_response.txt -w "%{http_code}" -H "ngrok-skip-browser-warning: true" "$URL/api/health" 2>/dev/null)
if [ "$CODE" = "200" ]; then
  echo "   OK — ngrok работает, ответ: $(cat /tmp/ngrok_response.txt)"
else
  echo "   ОШИБКА — код $CODE. Убедитесь что ngrok запущен: ngrok http 8000"
  echo "   Текущий URL в конфиге может быть старым — скопируйте новый URL из окна ngrok и обновите config.py и calendar.js"
  exit 1
fi

echo ""
echo "Всё в порядке. Календарь должен загружать данные с этого URL."
