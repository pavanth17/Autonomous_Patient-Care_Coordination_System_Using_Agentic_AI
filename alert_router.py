class AlertRouter:
    """A minimal stub for alert routing.

    In the full application this would contain logic to determine which staff
    members should receive a given alert based on severity, patient assignment,
    and other business rules. For the purpose of getting the application to
    start we provide a simple implementation that returns an empty list.
    """

    def distribute_alert(self, patient_id, alert_severity, alert_id):
        """Return a list of StaffMember recipients for the alert.

        Args:
            patient_id (int): Identifier of the patient triggering the alert.
            alert_severity (str): Severity level of the alert (e.g., "high").
            alert_id (int): Identifier of the alert instance.
        Returns:
            list: Empty list placeholder for staff recipients.
        """
        # Placeholder implementation – extend with real routing logic.
        return []

# Export a singleton instance as used by the rest of the codebase.
alert_router = AlertRouter()

try:
    from services import send_to_n8n_webhook
except ImportError:
    def send_to_n8n_webhook(alert):
        return False

