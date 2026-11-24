"""ITSM providers: tickets, attachments, comments and closure.

ServiceNow, Jira (Cloud and Data Center / Jira Service Management),
Freshservice, BMC Helix ITSM (Remedy) and a fake for demo mode, behind one
interface. Used by the Excel config push to read the workbook attached to
a ticket and to attach the updated workbook (with backups) back to it.
"""

from vireonet.itsm.base import Attachment, ItsmError, ItsmProvider, Ticket

__all__ = ["Attachment", "ItsmError", "ItsmProvider", "Ticket"]
