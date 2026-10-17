"""
Script to permanently remove test/demo staff accounts from the database.
Test accounts: DOC_RT, NRS_RT, NRS_ICU, NRS_ER

Uses direct SQLite3 to handle FK constraints gracefully.
"""
import sys
import os
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_STAFF_IDS = ['DOC_RT', 'NRS_RT', 'NRS_ICU', 'NRS_ER']

# Adjust path to the database file
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'patient_care_dev.db')


def remove_test_users():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")  # Bypass FK constraints for cleanup
    cur = conn.cursor()

    removed = []
    for staff_id in TEST_STAFF_IDS:
        cur.execute("SELECT id, first_name, last_name FROM staff_members WHERE staff_id = ?", (staff_id,))
        row = cur.fetchone()
        if row:
            pk, first, last = row
            full_name = f"{first} {last}"
            print(f"Found: {staff_id} ({full_name}, id={pk}) — removing...")

            # Nullify audit_log references (delete those rows instead)
            cur.execute("DELETE FROM audit_logs WHERE changed_by_id = ?", (pk,))
            print(f"  Deleted {cur.rowcount} audit log entries referencing {staff_id}")

            # Unassign patients
            cur.execute("UPDATE patients SET assigned_doctor_id = NULL WHERE assigned_doctor_id = ?", (pk,))
            cur.execute("UPDATE patients SET assigned_nurse_id = NULL WHERE assigned_nurse_id = ?", (pk,))

            # Delete shifts, handoffs referencing this staff member
            cur.execute("DELETE FROM shifts WHERE staff_id = ?", (pk,))
            cur.execute("DELETE FROM shift_handoffs WHERE outgoing_staff_id = ? OR incoming_staff_id = ?", (pk, pk))

            # Finally delete the staff member
            cur.execute("DELETE FROM staff_members WHERE id = ?", (pk,))
            removed.append(staff_id)
            print(f"  [OK] Removed {staff_id}")
        else:
            print(f"Not found (skipping): {staff_id}")

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    if removed:
        print(f"\n[DONE] Successfully removed {len(removed)} test account(s): {', '.join(removed)}")
    else:
        print("\nNo test accounts found in database.")


if __name__ == '__main__':
    remove_test_users()
