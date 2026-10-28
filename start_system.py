"""
ASHA Pregnancy Tracking System - One-Click System Launcher
Starts the FastAPI backend and serves the offline-first Progressive Web Application.
"""
import os
import sys
import uvicorn

# Add current workspace to path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print("=" * 70)
    print("   ASHA Pregnancy Tracking System (Offline-First Maternal Health)")
    print("=" * 70)
    print(f"Server URL:               http://localhost:{port}")
    print(f"Swagger API Docs:         http://localhost:{port}/docs")
    print(f"Mobile PWA App:           http://localhost:{port}/")
    print(f"Pre-loaded demo accounts:")
    print(f"  • ASHA Worker:          asha_lakshmi     / AshaPass@123")
    print(f"  • ANM Nurse:            anm_meena        / AnmPass@123")
    print(f"  • Medical Officer:      mo_suresh        / DoctorPass@123")
    print(f"  • Supervisor:           supervisor_radha / SuperPass@123")
    print(f"  • System Admin:         admin_system     / AdminPass@123")
    print("=" * 70)
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=port, reload=False)

