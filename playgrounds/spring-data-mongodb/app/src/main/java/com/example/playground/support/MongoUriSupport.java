package com.example.playground.support;

import java.util.ArrayList;
import java.util.List;

/**
 * Normalizes a DocumentDB connection string for the MongoDB <b>Java</b> driver.
 *
 * <p>The sibling playgrounds (Node.js / Python) connect with
 * {@code tlsAllowInvalidCertificates=true}, but the Java driver does not
 * recognize that option, and its {@code tlsInsecure} option only disables
 * <em>hostname</em> verification — not certificate-chain validation. So this
 * helper strips {@code tlsAllowInvalidCertificates} (the emulator's self-signed
 * certificate is instead trusted via an SSLContext in {@link MongoClientFactory})
 * and removes {@code replicaSet}, which conflicts with {@code directConnection=true}
 * against the standalone gateway.
 */
public final class MongoUriSupport {

    public static final String DEFAULT_URI =
            "mongodb://docdbadmin:Documentdb!Local1@localhost:10260/"
                    + "?tls=true&tlsAllowInvalidCertificates=true&directConnection=true";

    private MongoUriSupport() {
    }

    /** Returns a Java-driver-compatible URI with unsupported options removed. */
    public static String sanitize(String uri) {
        if (uri == null || uri.isBlank()) {
            uri = DEFAULT_URI;
        }

        int queryStart = uri.indexOf('?');
        if (queryStart < 0) {
            return uri;
        }

        String base = uri.substring(0, queryStart);
        String[] params = uri.substring(queryStart + 1).split("&");
        List<String> kept = new ArrayList<>();

        for (String param : params) {
            if (param.isEmpty()) {
                continue;
            }
            int eq = param.indexOf('=');
            String key = eq < 0 ? param : param.substring(0, eq);

            if (key.equalsIgnoreCase("replicaSet")
                    || key.equalsIgnoreCase("tlsAllowInvalidCertificates")) {
                continue;
            }
            kept.add(param);
        }

        return kept.isEmpty() ? base : base + "?" + String.join("&", kept);
    }
}
