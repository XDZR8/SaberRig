# Security Policy / Política de seguridad

## English

### Supported Versions

| Version | Supported          |
|---------|--------------------|
| 1.0.5   | ✅ Yes (current)   |
| < 1.0.5 | ❌ No              |

Only the latest 1.0.x releases receive security updates. Older versions are not supported.

### Reporting a Vulnerability

**Please do not open a public GitHub issue or pull request for security vulnerabilities.**

Report privately using one of these methods (preferred order):

1. **GitHub private vulnerability reporting** (recommended)  
   Go to the repository → Security tab → Advisories → “New draft security advisory”  
   or use: https://github.com/XDZR8/SaberRig/security/advisories/new

2. Contact me privately through GitHub.

Please include:
- SaberRig version
- Blender version
- Operating system
- Clear steps to reproduce
- Expected vs actual behavior
- Any relevant console output (without personal paths or sensitive data if possible)

I'll try to acknowledge the report as soon as possible. Critical issues that could affect user data or cause unexpected file system access will be prioritized.

### Scope

SaberRig is a local Blender add-on. It does not make network requests, collect telemetry, or send data to external servers.

Relevant security concerns may include:
- Unexpected or overly broad file system access
- Unintended creation or modification of files next to the .blend
- Information disclosure through audit reports or console output (local paths that may reveal usernames or project structure)

Issues that only affect visual results, bone mapping, or require the user to already have full control of the machine are generally outside the security scope.

---

## Español

### Versiones soportadas

| Versión | Soporte            |
|---------|--------------------|
| 1.0.5   | ✅ Sí (actual)     |
| < 1.0.5 | ❌ No              |

Solo las versiones 1.0.x más recientes reciben actualizaciones de seguridad. Las versiones anteriores no están soportadas.

### Cómo reportar una vulnerabilidad

**Por favor, no abras un issue o pull request público en GitHub para vulnerabilidades de seguridad.**

Reporta de forma privada usando uno de estos métodos (en orden de preferencia):

1. **Informe privado de vulnerabilidad de GitHub** (recomendado)  
   Ve al repositorio → pestaña Security → Advisories → “New draft security advisory”  
   o usa: https://github.com/XDZR8/SaberRig/security/advisories/new

2. Contáctame de forma privada a través de GitHub.

Incluye, si es posible:
- Versión de SaberRig
- Versión de Blender
- Sistema operativo
- Pasos claros para reproducir el problema
- Comportamiento esperado vs real
- Salida relevante de la consola (sin rutas personales o datos sensibles si es posible)

Intentaré solventarlo lo antes posible. Los problemas críticos que puedan afectar datos del usuario o causar acceso inesperado al sistema de archivos tendrán prioridad.

### Alcance

SaberRig es un addon local de Blender. No realiza solicitudes de red, no recoge telemetría ni envía datos a servidores externos.

Posibles preocupaciones de seguridad relevantes:
- Acceso inesperado o demasiado amplio al sistema de archivos
- Creación o modificación no intencionada de archivos junto al .blend
- Divulgación de información a través de informes de auditoría o la consola (rutas locales que pueden revelar nombres de usuario o estructura del proyecto)

Los problemas que solo afectan al resultado visual, al mapeo de huesos o que requieren que el usuario ya tenga control total de la máquina suelen quedar fuera del alcance de seguridad.
