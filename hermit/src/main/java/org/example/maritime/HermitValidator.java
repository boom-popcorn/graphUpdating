package org.example.maritime;

import java.io.File;
import java.util.ArrayList;
import java.util.List;

import org.semanticweb.HermiT.Reasoner;
import org.semanticweb.owlapi.apibinding.OWLManager;
import org.semanticweb.owlapi.model.IRI;
import org.semanticweb.owlapi.model.OWLOntology;
import org.semanticweb.owlapi.model.OWLOntologyManager;
import org.semanticweb.owlapi.reasoner.OWLReasoner;

public final class HermitValidator {
    private HermitValidator() {}

    public static void main(String[] args) {
        if (args.length != 1) {
            System.err.println("Usage: HermitValidator <ontology.ttl>");
            System.exit(2);
        }

        OWLReasoner reasoner = null;
        try {
            OWLOntologyManager manager = OWLManager.createOWLOntologyManager();
            OWLOntology ontology = manager.loadOntologyFromOntologyDocument(new File(args[0]));
            reasoner = new Reasoner.ReasonerFactory().createReasoner(ontology);
            boolean consistent = reasoner.isConsistent();
            List<String> unsatisfiable = new ArrayList<String>();
            if (consistent) {
                for (org.semanticweb.owlapi.model.OWLClass cls : reasoner.getUnsatisfiableClasses().getEntitiesMinusBottom()) {
                    unsatisfiable.add(jsonString(shortForm(cls.getIRI())));
                }
            }
            System.out.println("{\"engine\":\"HermiT\",\"consistent\":" + consistent
                    + ",\"unsatisfiableClasses\":[" + join(unsatisfiable) + "]}");
        } catch (Throwable error) {
            error.printStackTrace(System.err);
            System.exit(3);
        } finally {
            if (reasoner != null) {
                reasoner.dispose();
            }
        }
    }

    private static String shortForm(IRI iri) {
        String value = iri.toString();
        int split = Math.max(value.lastIndexOf('#'), Math.max(value.lastIndexOf('/'), value.lastIndexOf(':')));
        return split >= 0 ? value.substring(split + 1) : value;
    }

    private static String join(List<String> values) {
        StringBuilder result = new StringBuilder();
        for (int i = 0; i < values.size(); i++) {
            if (i > 0) result.append(',');
            result.append(values.get(i));
        }
        return result.toString();
    }

    private static String jsonString(String value) {
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }
}
