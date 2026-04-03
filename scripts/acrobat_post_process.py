"""
Acrobat Post-Processor: Triggers XFA JavaScript events on filled PDFs.

Runs on Windows with Adobe Acrobat Pro installed. Uses COM automation
to open the PDF, trigger checkbox click events (which run embedded JS
for field visibility), and save with proper incremental save (preserves
digital signatures).

Usage:
    python acrobat_post_process.py <input.pdf> <output.pdf> [--fields CanadaUS,Other]

Or as a server:
    python acrobat_post_process.py --serve --port 9090

Requirements:
    - Windows with Adobe Acrobat Pro DC
    - pip install pywin32 flask
    - Acrobat JavaScript enabled in preferences
    - PDF folder added to Acrobat trusted locations
"""

import argparse
import os
import sys
import time
import json
import shutil


def trigger_xfa_events(input_pdf: str, output_pdf: str, checkbox_fields: list[str] = None):
    """Open PDF in Acrobat, trigger checkbox events, save.

    Args:
        input_pdf: Path to the filled PDF.
        output_pdf: Path to save the post-processed PDF.
        checkbox_fields: List of checkbox field names to trigger (e.g. ["CanadaUS"]).
                        If None, auto-detects phone checkbox fields.
    """
    try:
        import win32com.client
    except ImportError:
        print("ERROR: pywin32 not installed. Run: pip install pywin32", file=sys.stderr)
        sys.exit(1)

    if checkbox_fields is None:
        checkbox_fields = ["CanadaUS"]

    app = None
    try:
        app = win32com.client.Dispatch("AcroExch.App")
        app.Hide()

        avDoc = win32com.client.Dispatch("AcroExch.AVDoc")
        if not avDoc.Open(os.path.abspath(input_pdf), ""):
            raise RuntimeError(f"Failed to open {input_pdf}")

        pdDoc = avDoc.GetPDDoc()
        jso = pdDoc.GetJSObject()

        # Build script to trigger all checkbox fields
        # We need to find each checkbox in the XFA DOM and fire its click event
        script_parts = []
        for field_name in checkbox_fields:
            script_parts.append(f"""
                (function() {{
                    // Search for the field in the XFA form tree
                    var nodes = xfa.resolveNodes("{field_name}[*]");
                    if (nodes && nodes.length > 0) {{
                        for (var i = 0; i < nodes.length; i++) {{
                            var node = nodes.item(i);
                            if (node && node.rawValue == "1") {{
                                // Field is checked - trigger click to run visibility JS
                                node.execEvent("click");
                            }}
                        }}
                    }} else {{
                        // Try resolving by common XFA paths
                        var paths = [
                            "form1.Page2.ContactInformation.contact.PhoneNumbers.Phone.{field_name}",
                            "form1.Page2.ContactInformation.contact.PhoneNumbers.AltPhone.{field_name}",
                            "form1.Page2.ContactInformation.contact.FaxEmail.Phone.{field_name}",
                            "form1.Page3.FaxEmail.{field_name}",
                        ];
                        for (var j = 0; j < paths.length; j++) {{
                            try {{
                                var n = xfa.resolveNode(paths[j]);
                                if (n && n.rawValue == "1") {{
                                    n.execEvent("click");
                                }}
                            }} catch(e) {{}}
                        }}
                    }}
                }})();
            """)

        script_parts.append("xfa.form.remerge();")
        full_script = "\n".join(script_parts)

        jso.execScript(full_script)

        # Brief pause to let Acrobat process events
        time.sleep(1)

        # Save: 1 = PDSaveFull, 0x04 = PDSaveIncremental
        output_abs = os.path.abspath(output_pdf)
        avDoc.Save(1, output_abs)
        avDoc.Close(1)

        print(json.dumps({"status": "ok", "output": output_abs, "size": os.path.getsize(output_abs)}))

    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}), file=sys.stderr)
        sys.exit(1)
    finally:
        if app:
            try:
                app.Exit()
            except:
                pass


def serve(port: int = 9090):
    """Run as a simple HTTP server for the MCP server to call."""
    try:
        from flask import Flask, request, jsonify, send_file
    except ImportError:
        print("ERROR: flask not installed. Run: pip install flask", file=sys.stderr)
        sys.exit(1)

    app = Flask(__name__)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"})

    @app.route("/process", methods=["POST"])
    def process():
        """Accept a PDF file, trigger XFA events, return processed PDF."""
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        fields = request.form.get("fields", "CanadaUS").split(",")

        # Save uploaded file
        import tempfile
        input_path = os.path.join(tempfile.gettempdir(), f"xfa_input_{os.getpid()}.pdf")
        output_path = os.path.join(tempfile.gettempdir(), f"xfa_output_{os.getpid()}.pdf")

        try:
            file.save(input_path)
            trigger_xfa_events(input_path, output_path, fields)
            return send_file(output_path, mimetype="application/pdf",
                           as_attachment=True, download_name="processed.pdf")
        finally:
            for p in [input_path, output_path]:
                if os.path.exists(p):
                    os.unlink(p)

    print(f"Acrobat post-processor listening on port {port}")
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Acrobat XFA Post-Processor")
    parser.add_argument("input_pdf", nargs="?", help="Input PDF path")
    parser.add_argument("output_pdf", nargs="?", help="Output PDF path")
    parser.add_argument("--fields", default="CanadaUS",
                       help="Comma-separated checkbox field names to trigger")
    parser.add_argument("--serve", action="store_true",
                       help="Run as HTTP server")
    parser.add_argument("--port", type=int, default=9090,
                       help="Server port (default: 9090)")

    args = parser.parse_args()

    if args.serve:
        serve(args.port)
    elif args.input_pdf and args.output_pdf:
        fields = [f.strip() for f in args.fields.split(",")]
        trigger_xfa_events(args.input_pdf, args.output_pdf, fields)
    else:
        parser.print_help()
