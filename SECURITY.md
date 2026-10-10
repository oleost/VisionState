# Security

Please report a vulnerability privately: **Security → Report a vulnerability** on this repository
(GitHub's private vulnerability reporting), not as a public issue. You get an answer within a few
days, and a fix goes out as a beta first, then in the next stable release.

Supported: the latest stable release and the current beta.

What VisionState relies on, so you know what counts as a vulnerability: it is only reachable
through Home Assistant Ingress (other requests are refused); camera credentials are never written to
logs, errors or exports; nothing is sent anywhere but to your own Home Assistant and MQTT broker.
