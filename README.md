# Docker Pause Manager

Auto pause/unpause Docker containers based on connection activity. Sleeps idle containers and wakes them on access.

[中文文档](README_zh-CN.md)

## Features

- **Auto Sleep**: Pause containers after idle timeout
- **Auto Wake**: Unpause containers when connections detected
- **Multi-Port**: Monitor multiple TCP/UDP ports per container
- **Multi-Language**: Support for Chinese (zh-CN) and English (en-US)
- **Lightweight**: Alpine-based Docker image, optimized for size

## Quick Start

### Docker Compose (Recommended)

```bash
git clone https://github.com/jiushizhu2024/docker-pause-manager.git
cd docker-pause-manager
docker-compose up -d
```

### Manual Docker Run

```bash
docker run -d \
  --name docker-pause-manager \
  -p 5287:5287 \
  -v /path/to/config.json:/app/config.json:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e JWT_SECRET=your-secret-key \
  --cap-add NET_ADMIN \
  --cap-add NET_RAW \
  ghcr.io/jiushizhu2024/docker-pause-manager:latest
```

## Configuration

Create `config.json`:

```json
{
  "watchers": [
    {
      "container": "myapp",
      "host_ports": [
        {"port": 8080, "protocol": "tcp"},
        {"port": 53, "protocol": "udp"}
      ],
      "idle_seconds": 300,
      "enabled": true
    }
  ],
  "settings": {
    "check_interval": 2,
    "listen_port": 5287
  }
}
```

## API Endpoints

- `GET /api/watchers` - List all watchers
- `POST /api/watchers` - Add new watcher
- `PUT /api/watchers/<name>` - Update watcher
- `DELETE /api/watchers/<name>` - Delete watcher
- `POST /api/watchers/<name>/pause` - Manually pause
- `POST /api/watchers/<name>/unpause` - Manually unpause
- `POST /api/watchers/<name>/start` - Manually start (exited containers)
- `GET /api/containers` - List Docker containers
- `GET /api/settings` - Get settings
- `POST /api/settings` - Update settings
- `GET /api/i18n/<lang>` - Get translations
- `GET /api/languages` - List supported languages

## Language Support

- Chinese (zh-CN)
- English (en-US)

Switch language via URL: `?lang=en-US` or via UI language selector.

## Security

- Runs as non-root user in container
- Minimal base image (Alpine)
- Read-only config volume
- Docker socket mounted read-only
- JWT-based admin authentication

## License

MIT
