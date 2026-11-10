"""Chikku AI — AI assistant subsystem of the amba_garments app.

Layers (imports only go downwards):
	api/        whitelisted HTTP entry points (thin)
	ai/         orchestration, prompts, language, greeting, rendering
	providers/  LLM provider adapters (no ERP knowledge)
	tools/      fixed tool registry; the only bridge from the model to ERP data
	context/    validation of the Desk context sent by the browser
	services/   ERP business logic, permission-aware, no LLM imports
	security/   access gate, limits, confirmation tokens, untrusted-content handling
	audit/      audit, tool-call and usage logging
	extensions/ service contracts for modules not yet implemented
"""

USER_ROLE = "Chikku AI User"
MANAGER_ROLE = "Chikku AI Manager"
SETTINGS = "Chikku AI Settings"
CONVERSATION = "Chikku AI Conversation"
MESSAGE = "Chikku AI Message"
ACTION_REQUEST = "Chikku AI Action Request"
PROVIDER = "Chikku AI Provider Configuration"
USER_PREFERENCE = "Chikku AI User Preference"
TOOL_CALL = "Chikku AI Tool Call"
USAGE_LOG = "Chikku AI Usage Log"
AUDIT_LOG = "Chikku AI Audit Log"

REALTIME_EVENT = "chikku_ai_event"
TURN_QUEUE = "long"
