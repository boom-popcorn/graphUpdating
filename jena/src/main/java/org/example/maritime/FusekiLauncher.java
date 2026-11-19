package org.example.maritime;

import com.google.gson.*;
import jakarta.servlet.http.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.Files;
import org.apache.jena.fuseki.main.FusekiServer;
import org.apache.jena.query.*;
import org.apache.jena.tdb2.TDB2Factory;

/** One process owns TDB2, serves official Fuseki queries and atomic application commits. */
public final class FusekiLauncher {
    public static void main(String[] args) throws Exception {
        if (args.length != 3) throw new IllegalArgumentException("Usage: FusekiLauncher DATASET_DIR PORT WEB_DIR");
        Path path = Path.of(args[0]).toAbsolutePath().normalize();
        // The ontology baseline must be available before the first update round.
        Path baseline = Path.of(args[2]).toAbsolutePath().resolve("../../ontology/00").normalize();
        Path storedBaseline = path.resolve("ontology/00");
        Files.createDirectories(storedBaseline);
        for (String filename : new String[]{"ontology.json", "ontology.ttl"}) {
            Path destination = storedBaseline.resolve(filename);
            if (!Files.exists(destination)) Files.copy(baseline.resolve(filename), destination);
        }
        Dataset ds = TDB2Factory.connectDataset(path.toString());
        // Seed a new dataset before serving either the graph canvas or SPARQL.
        // An existing snapshot is authoritative and must never be reset on restart.
        ds.begin(ReadWrite.WRITE);
        try {
            if (JenaStore.readDocument(ds).isJsonNull()) {
                Path initial = Path.of(args[2]).toAbsolutePath().resolve("../../data/initial_graph.json").normalize();
                try (Reader reader = Files.newBufferedReader(initial, StandardCharsets.UTF_8)) {
                    JenaStore.writeDocument(ds, JsonParser.parseReader(reader).getAsJsonObject());
                }
            }
            ds.commit();
        } catch (Exception ex) { ds.abort(); ds.close(); throw ex; }
        finally { if (ds.isInTransaction()) ds.end(); }
        FusekiServer server = FusekiServer.create().port(Integer.parseInt(args[1])).loopback(true)
            .add("/maritime", ds, false)
            .staticFileBase(Path.of(args[2]).toAbsolutePath().toString())
            .addServlet("/ontology_viewer.html", new HttpServlet() {
                @Override protected void doGet(HttpServletRequest req, HttpServletResponse res) throws IOException {
                    res.setContentType("text/html; charset=utf-8");
                    res.setHeader("Cache-Control", "no-store");
                    Files.copy(Path.of(args[2]).toAbsolutePath().resolve("../../ontology_viewer.html").normalize(), res.getOutputStream());
                }
            })
            .addServlet("/api/ontology/*", new HttpServlet() {
                @Override protected void doGet(HttpServletRequest req, HttpServletResponse res) throws IOException {
                    Path ontology = path.resolve("ontology");
                    res.setContentType("application/json; charset=utf-8");
                    res.setHeader("Cache-Control", "no-store");
                    String part = req.getPathInfo();
                    if (part == null || part.equals("/")) {
                        JsonArray versions = new JsonArray();
                        if (Files.isDirectory(ontology)) try (var dirs = Files.list(ontology)) {
                            dirs.filter(p -> p.getFileName().toString().matches("[0-9]+") && Files.isRegularFile(p.resolve("ontology.json")))
                                .sorted(java.util.Comparator.comparing(p -> new java.math.BigInteger(p.getFileName().toString())))
                                .forEach(p -> versions.add(p.getFileName().toString()));
                        }
                        JsonObject result = new JsonObject(); result.add("versions", versions);
                        res.getWriter().print(JenaStore.GSON.toJson(result));
                    } else if (part.matches("/[0-9]+")) {
                        Path file = ontology.resolve(part.substring(1)).resolve("ontology.json");
                        if (!Files.isRegularFile(file)) { res.sendError(404); return; }
                        Files.copy(file, res.getOutputStream());
                    } else { res.sendError(400); }
                }
            })
            .addServlet("/api/snapshot", new HttpServlet() {
                void reply(HttpServletResponse res, int status, JsonObject body) throws IOException {
                    res.setStatus(status); res.setContentType("application/json; charset=utf-8");
                    res.setHeader("Cache-Control", "no-store");
                    res.getWriter().print(JenaStore.GSON.toJson(body));
                }
                @Override protected void doGet(HttpServletRequest req, HttpServletResponse res) throws IOException {
                    ds.begin(ReadWrite.READ);
                    try {
                        JsonObject body = new JsonObject(); body.addProperty("ok", true);
                        body.addProperty("datasetDir", path.toString()); body.add("document", JenaStore.readDocument(ds));
                        reply(res, 200, body);
                    } finally { ds.end(); }
                }
                @Override protected void doPost(HttpServletRequest req, HttpServletResponse res) throws IOException {
                    if (req.getContentType() == null || !req.getContentType().toLowerCase().startsWith("application/json")) {
                        res.sendError(415); return;
                    }
                    JsonObject body = new JsonObject();
                    try {
                        JsonObject request = JsonParser.parseReader(new InputStreamReader(req.getInputStream(), StandardCharsets.UTF_8)).getAsJsonObject();
                        if (!request.has("expected") || !request.has("document") || !request.get("document").isJsonObject())
                            throw new IllegalArgumentException("expected snapshot and document required");
                        ds.begin(ReadWrite.WRITE);
                        try {
                            if (!JenaStore.readDocument(ds).equals(request.get("expected"))) {
                                body.addProperty("ok", false); body.addProperty("error", "snapshot conflict; reload and replan");
                                ds.abort(); reply(res, 409, body); return;
                            }
                            JenaStore.writeDocument(ds, request.getAsJsonObject("document"));
                            if (request.has("failBeforeCommit") && request.get("failBeforeCommit").getAsBoolean())
                                throw new IllegalStateException("injected failure after RDF writes before native commit");
                            ds.commit(); body.addProperty("ok", true); reply(res, 200, body);
                        } catch (Exception ex) { ds.abort(); throw ex; }
                        finally { ds.end(); }
                    } catch (Exception ex) {
                        body.addProperty("ok", false); body.addProperty("error", ex.toString()); reply(res, 500, body);
                    }
                }
            }).build();
        Runtime.getRuntime().addShutdownHook(new Thread(() -> { server.stop(); ds.close(); }));
        server.start();
        System.out.println("Fuseki query console: " + server.serverURL());
        System.out.println("TDB2 dataset: " + path);
        server.join();
    }
}
