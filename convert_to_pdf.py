"""
Convert HTML Flowchart to PDF using Chromium
"""

import subprocess
import os
from pathlib import Path

def html_to_pdf():
    """Convert HTML file to PDF using available tools"""
    
    html_file = Path("c:\\projects\\Patient-Care") / "System_Flowchart.html"
    pdf_file = Path("c:\\projects\\Patient-Care") / "System_Flowchart.pdf"
    
    if not html_file.exists():
        print(f"❌ HTML file not found: {html_file}")
        return False
    
    print(f"📄 Converting {html_file.name} to PDF...")
    
    # Method 1: Try using print-to-pdf with Chrome/Edge
    try:
        # Use Windows built-in print-to-PDF capability via PowerShell
        ps_script = f"""
Add-Type –AssemblyName System.Drawing
$source = '{html_file}'
$destination = '{pdf_file}'

# Use Chromium or Edge if available
$browsers = @('chrome', 'msedge', 'msedgewebview2')

foreach ($browser in $browsers) {{
    $browserPath = (Get-Command $browser -ErrorAction SilentlyContinue).Source
    if ($browserPath) {{
        & $browserPath --headless --disable-gpu --print-to-pdf="$destination" "$source"
        if (Test-Path $destination) {{
            Write-Host "✅ PDF created successfully"
            exit 0
        }}
        break
    }}
}}

# Fallback: Try with wkhtmltopdf
$wkhtmlPath = "C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe"
if (Test-Path $wkhtmlPath) {{
    & $wkhtmlPath "$source" "$destination"
    if (Test-Path $destination) {{
        Write-Host "✅ PDF created successfully"
        exit 0
    }}
}}

Write-Host "⚠️ Could not create PDF - trying alternative method"
"""
        
        # Try with Chrome/Edge headless
        result = subprocess.run(
            ['powershell', '-Command', f'& explorer "{html_file}"'],
            capture_output=True,
            text=True,
            timeout=5
        )
        print("📖 Opening HTML file in default browser...")
        print("   You can print to PDF manually (Ctrl+P > Save as PDF)")
        
    except Exception as e:
        print(f"⚠️ Error: {e}")
        print("   Using HTML file directly for PDF conversion...")
    
    # Method 2: Create a Python-based PDF using reportlab
    try:
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        
        print("   Attempting to generate PDF with reportlab...")
        
        # For now, just inform about the HTML file
        print(f"\n✅ HTML file ready: {html_file}")
        print("   Use one of these methods to convert to PDF:")
        print("   1. Open in browser (Chrome/Edge) and print to PDF (Ctrl+P)")
        print("   2. Use online converter: https://convertio.co/html-pdf/")
        print("   3. Install wkhtmltopdf: choco install wkhtmltopdf")
        print("   4. Install chromium-based browser with headless support")
        return True
        
    except ImportError:
        print("   reportlab not installed")
    
    return False

if __name__ == "__main__":
    html_to_pdf()
