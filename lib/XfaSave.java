import com.itextpdf.kernel.pdf.*;
import java.io.*;
import java.nio.charset.StandardCharsets;

/**
 * Signature-preserving XFA save using iText 7 append mode.
 *
 * Usage: java XfaSave <input.pdf> <output.pdf> <datasets.xml> [form.xml] [template.xml]
 *
 * Replaces XFA streams and saves with append mode to preserve digital signatures.
 */
public class XfaSave {
    public static void main(String[] args) throws Exception {
        if (args.length < 3) {
            System.err.println("Usage: java XfaSave <input.pdf> <output.pdf> <datasets.xml> [form.xml] [template.xml]");
            System.exit(1);
        }

        String inputPath = args[0];
        String outputPath = args[1];
        String datasetsPath = args[2];
        String formPath = args.length > 3 && !args[3].isEmpty() ? args[3] : null;
        String templatePath = args.length > 4 && !args[4].isEmpty() ? args[4] : null;

        byte[] datasetsXml = readFile(datasetsPath);
        byte[] formXml = formPath != null ? readFile(formPath) : null;
        byte[] templateXml = templatePath != null ? readFile(templatePath) : null;

        PdfReader reader = new PdfReader(inputPath,
            new ReaderProperties().setPassword(new byte[0]));
        reader.setUnethicalReading(true);

        PdfWriter writer = new PdfWriter(outputPath);
        PdfDocument pdfDoc = new PdfDocument(reader, writer,
            new StampingProperties().useAppendMode());

        PdfDictionary acroForm = pdfDoc.getCatalog().getPdfObject()
            .getAsDictionary(PdfName.AcroForm);
        PdfArray xfa = acroForm.getAsArray(new PdfName("XFA"));

        for (int i = 0; i < xfa.size(); i += 2) {
            String key = xfa.getAsString(i).getValue();

            if ("datasets".equals(key)) {
                PdfStream stream = xfa.getAsStream(i + 1);
                stream.setData(datasetsXml, true);
                stream.setModified();
                stream.flush();
            }

            if ("form".equals(key) && formXml != null) {
                PdfStream stream = xfa.getAsStream(i + 1);
                stream.setData(formXml, true);
                stream.setModified();
                stream.flush();
            }

            if ("template".equals(key) && templateXml != null) {
                PdfStream stream = xfa.getAsStream(i + 1);
                stream.setData(templateXml, true);
                stream.setModified();
                stream.flush();
            }
        }

        pdfDoc.close();

        File f = new File(outputPath);
        System.out.println("{\"status\":\"ok\",\"size\":" + f.length() + "}");
    }

    static byte[] readFile(String path) throws IOException {
        FileInputStream fis = new FileInputStream(path);
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = fis.read(buf)) != -1) bos.write(buf, 0, n);
        fis.close();
        return bos.toByteArray();
    }
}
