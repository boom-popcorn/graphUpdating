package org.example.maritime;

import com.google.gson.*;
import java.io.*;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import org.apache.jena.query.*;
import org.apache.jena.rdf.model.*;
import org.apache.jena.tdb2.TDB2Factory;
import org.apache.jena.vocabulary.RDF;

/** Private line-delimited JSON bridge. The public tool transport is MCP, in Python. */
public final class JenaStore {
    static final String NS = "https://example.org/maritime#";
    static final String PROV = "http://www.w3.org/ns/prov#";
    static final String DATA = "urn:maritime:data", META = "urn:maritime:metadata", COMMITS = "urn:maritime:commits";
    static final Gson GSON = new GsonBuilder().serializeNulls().create();
    static Resource resource(String id) { return ResourceFactory.createResource(NS + URLEncoder.encode(id, StandardCharsets.UTF_8).replace("+", "%20")); }
    static Property property(String id) { return ResourceFactory.createProperty(resource(id).getURI()); }
    static void value(Model m, Resource s, String p, JsonElement v) {
        if (v == null || v.isJsonNull()) return;
        RDFNode n;
        if (v.isJsonPrimitive() && v.getAsJsonPrimitive().isBoolean()) n = m.createTypedLiteral(v.getAsBoolean());
        else if (v.isJsonPrimitive() && v.getAsJsonPrimitive().isNumber()) n = m.createTypedLiteral(v.getAsDouble());
        else n = m.createLiteral(v.isJsonPrimitive() ? v.getAsString() : GSON.toJson(v));
        m.add(s, property(p), n);
    }
    static Model data(JsonObject doc) {
        Model m = ModelFactory.createDefaultModel();
        for (var entry : doc.getAsJsonObject("nodes").entrySet()) {
            Resource s = resource(entry.getKey()); JsonObject node = entry.getValue().getAsJsonObject();
            m.add(s, property("id"), entry.getKey());
            for (JsonElement t : node.getAsJsonArray("types")) m.add(s, RDF.type, resource(t.getAsString()));
            for (var field : node.entrySet()) {
                if (field.getKey().equals("properties")) {
                    for (var p : field.getValue().getAsJsonObject().entrySet()) value(m, s, p.getKey(), p.getValue());
                } else if (field.getKey().equals("evidence")) {
                    for (JsonElement e : field.getValue().getAsJsonArray()) m.add(s, ResourceFactory.createProperty(PROV + "used"), resource(e.getAsString()));
                } else if (!field.getKey().equals("types")) value(m, s, field.getKey(), field.getValue());
            }
        }
        for (JsonElement e : doc.getAsJsonArray("relations")) {
            JsonObject r = e.getAsJsonObject(); m.add(resource(r.get("s").getAsString()), property(r.get("p").getAsString()), resource(r.get("o").getAsString()));
        }
        if (doc.has("temporalValues")) for (var e : doc.getAsJsonObject("temporalValues").entrySet()) value(m, resource(e.getKey()), "timestamp", e.getValue());
        if (doc.has("evidence")) for (var e : doc.getAsJsonObject("evidence").entrySet()) {
            Resource s = resource(e.getKey()); m.add(s, RDF.type, ResourceFactory.createResource(PROV + "Entity"));
            for (var p : e.getValue().getAsJsonObject().entrySet()) value(m, s, p.getKey(), p.getValue());
        }
        return m;
    }
    static Model commits(JsonObject doc) {
        Model m = ModelFactory.createDefaultModel();
        if (doc.has("commits")) for (JsonElement e : doc.getAsJsonArray("commits")) {
            JsonObject c = e.getAsJsonObject(); Resource s = resource(c.get("commitId").getAsString());
            m.add(s, RDF.type, ResourceFactory.createResource(PROV + "Activity"));
            for (var p : c.entrySet()) value(m, s, p.getKey(), p.getValue());
        }
        if (doc.has("appliedPlans")) for (var e : doc.getAsJsonObject("appliedPlans").entrySet()) value(m, resource(e.getKey()), "appliedResult", e.getValue());
        return m;
    }
    static void replaceDelta(Model target, Model next) {
        Model remove = target.difference(next), add = next.difference(target);
        target.remove(remove); target.add(add);
    }
    static JsonElement readDocument(Dataset ds) {
        Statement st = ds.getNamedModel(META).getProperty(resource("snapshot"), property("document"));
        return st == null ? JsonNull.INSTANCE : JsonParser.parseString(st.getString());
    }
    static void writeDocument(Dataset ds, JsonObject doc) {
        replaceDelta(ds.getNamedModel(DATA), data(doc));
        replaceDelta(ds.getNamedModel(COMMITS), commits(doc));
        Model meta = ds.getNamedModel(META); meta.removeAll(resource("snapshot"), property("document"), null);
        meta.add(resource("snapshot"), property("document"), GSON.toJson(doc));
    }
    public static void main(String[] args) throws Exception {
        Dataset ds = TDB2Factory.connectDataset(args[0]);
        try (BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
             PrintWriter out = new PrintWriter(new OutputStreamWriter(System.out, StandardCharsets.UTF_8), true)) {
            String line;
            while ((line = in.readLine()) != null) {
                JsonObject response = new JsonObject();
                try {
                    JsonObject request = JsonParser.parseString(line).getAsJsonObject();
                    String op = request.get("op").getAsString();
                    switch (op) {
                        case "begin": ds.begin(ReadWrite.WRITE); break;
                        case "read": {
                            boolean own = !ds.isInTransaction(); if (own) ds.begin(ReadWrite.READ);
                            try {
                                response.add("document", readDocument(ds));
                            } finally { if (own) ds.end(); }
                            break;
                        }
                        case "commit": {
                            if (!ds.isInTransaction()) throw new IllegalStateException("begin required");
                            JsonObject doc = request.getAsJsonObject("document");
                            writeDocument(ds, doc);
                            if (request.has("failBeforeCommit") && request.get("failBeforeCommit").getAsBoolean())
                                throw new IllegalStateException("injected failure after RDF writes before native commit");
                            ds.commit(); ds.end(); break;
                        }
                        case "abort": if (ds.isInTransaction()) { ds.abort(); ds.end(); } break;
                        case "select": {
                            ds.begin(ReadWrite.READ);
                            try (QueryExecution q = QueryExecutionFactory.create(request.get("query").getAsString(), ds)) {
                                ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                                ResultSetFormatter.outputAsJSON(bytes, q.execSelect());
                                response.add("result", JsonParser.parseString(bytes.toString(StandardCharsets.UTF_8)));
                            } finally { ds.end(); } break;
                        }
                        default: throw new IllegalArgumentException("unknown operation " + op);
                    }
                    response.addProperty("ok", true);
                } catch (Exception ex) {
                    if (ds.isInTransaction()) { ds.abort(); ds.end(); }
                    response.addProperty("ok", false); response.addProperty("error", ex.toString());
                }
                out.println(GSON.toJson(response));
            }
            if (ds.isInTransaction()) { ds.abort(); ds.end(); }
        } finally { ds.close(); }
    }
}
