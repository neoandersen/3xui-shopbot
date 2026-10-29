#!/usr/bin/env bash
# ==============================================================================
# 3X-UI ShopBot — Nginx Overflow Shield Deployment Script
# File: deploy/nginx/install_security_hardening.sh
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

TARGET_DIR="/etc/nginx/conf.d"
TARGET_FILE="${TARGET_DIR}/security_hardening.conf"

SOURCE_FILE="${SCRIPT_DIR}/security_hardening.conf"
if [[ ! -f "${SOURCE_FILE}" ]]; then
    SOURCE_FILE="${PROJECT_ROOT}/src/shop_bot/config/security_hardening.conf"
fi

echo "========================================================================"
echo "🛡️  NEO Network — Nginx Buffer & Overflow Shield Installer"
echo "🕒  Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "📂  Source Config: ${SOURCE_FILE}"
echo "🎯  Target Path:   ${TARGET_FILE}"
echo "========================================================================"

# Check root or sudo
SUDO=""
if [[ $EUID -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        echo "❌ Ошибка: скрипт должен выполняться с правами root или через sudo."
        exit 1
    fi
fi

if [[ ! -f "${SOURCE_FILE}" ]]; then
    echo "❌ Ошибка: исходный файл конфигурации не найден по пути ${SOURCE_FILE}"
    exit 1
fi

if ! command -v nginx >/dev/null 2>&1; then
    echo "❌ Ошибка: Nginx не установлен на данном сервере."
    exit 1
fi

# 1. Создание целевых каталогов и журналов (для хоста и проброса в Docker :ro)
$SUDO mkdir -p "${TARGET_DIR}" /var/log/nginx
if [[ ! -f /var/log/nginx/access.log ]]; then
    $SUDO touch /var/log/nginx/access.log
    $SUDO chmod 644 /var/log/nginx/access.log 2>/dev/null || true
fi
if [[ ! -f /var/log/nginx/error.log ]]; then
    $SUDO touch /var/log/nginx/error.log
    $SUDO chmod 644 /var/log/nginx/error.log 2>/dev/null || true
fi

# 2. Бэкап существующего файла, если он уже присутствует
BACKUP_FILE=""
if [[ -f "${TARGET_FILE}" ]]; then
    BACKUP_FILE="${TARGET_FILE}.bak_$(date +%Y%m%d_%H%M%S)"
    $SUDO cp "${TARGET_FILE}" "${BACKUP_FILE}"
    echo "📦 Создан бэкап существующей конфигурации: ${BACKUP_FILE}"
fi

# 3. Копирование статического файла
$SUDO cp "${SOURCE_FILE}" "${TARGET_FILE}"
$SUDO chmod 644 "${TARGET_FILE}"
echo "📄 Конфигурационный файл скопирован в ${TARGET_FILE}"

# 4. Валидация синтаксиса через nginx -t
echo "🔍 Проверка синтаксиса конфигурации Nginx (nginx -t)..."
if $SUDO nginx -t; then
    echo "✔ Синтаксис Nginx корректен."
    
    # 5. Мягкая перезагрузка Nginx
    echo "🔄 Перезагрузка Nginx..."
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then
        $SUDO systemctl reload nginx
    else
        $SUDO nginx -s reload
    fi
    echo "========================================================================"
    echo "✅ Nginx Overflow Shield успешно установлен и активирован!"
    echo "========================================================================"
    exit 0
else
    echo "❌ Ошибка проверки конфигурации Nginx!"
    echo "⚠️  Запуск процедуры автоматического отката (Fail-Safe Rollback)..."
    
    if [[ -n "${BACKUP_FILE}" && -f "${BACKUP_FILE}" ]]; then
        $SUDO cp "${BACKUP_FILE}" "${TARGET_FILE}"
        echo "✔ Восстановлен предыдущий файл из бэкапа."
    else
        $SUDO rm -f "${TARGET_FILE}"
        echo "✔ Некорректный файл удалён."
    fi
    
    # Финальная проверка работоспособности после отката
    $SUDO nginx -t || true
    echo "========================================================================"
    echo "❌ Установка прервана. Nginx возвращён в исходное состояние."
    echo "========================================================================"
    exit 1
fi
