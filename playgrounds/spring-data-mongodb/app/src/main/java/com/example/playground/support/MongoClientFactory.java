package com.example.playground.support;

import java.security.SecureRandom;
import java.security.cert.X509Certificate;

import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

import com.mongodb.ConnectionString;
import com.mongodb.MongoClientSettings;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;

/**
 * Builds a {@link MongoClient} configured for the local DocumentDB emulator.
 *
 * <p>The emulator serves a self-signed certificate. The MongoDB Java driver
 * cannot bypass certificate-chain validation via the connection string, so when
 * running in insecure mode ({@code TLS_INSECURE != false}) this factory installs
 * a trust-all {@link SSLContext} and allows invalid hostnames — the Java
 * equivalent of the other drivers' {@code tlsAllowInvalidCertificates=true}.
 *
 * <p>For a real deployment, set {@code TLS_INSECURE=false} and configure a
 * truststore containing the server's CA instead.
 */
public final class MongoClientFactory {

    private MongoClientFactory() {
    }

    public static boolean insecureFromEnv() {
        return !"false".equalsIgnoreCase(
                System.getenv().getOrDefault("TLS_INSECURE", "true"));
    }

    /** Builds a client from {@code MONGO_URI}/{@code TLS_INSECURE} in the environment. */
    public static MongoClient fromEnv() {
        return create(System.getenv("MONGO_URI"), insecureFromEnv());
    }

    public static MongoClient create(String rawUri, boolean insecure) {
        ConnectionString connectionString = new ConnectionString(MongoUriSupport.sanitize(rawUri));
        MongoClientSettings.Builder settings = MongoClientSettings.builder()
                .applyConnectionString(connectionString);

        if (insecure) {
            SSLContext sslContext = trustAllContext();
            settings.applyToSslSettings(ssl -> ssl
                    .enabled(true)
                    .invalidHostNameAllowed(true)
                    .context(sslContext));
        }

        return MongoClients.create(settings.build());
    }

    private static SSLContext trustAllContext() {
        TrustManager[] trustAll = new TrustManager[] {
            new X509TrustManager() {
                @Override
                public void checkClientTrusted(X509Certificate[] chain, String authType) {
                }

                @Override
                public void checkServerTrusted(X509Certificate[] chain, String authType) {
                }

                @Override
                public X509Certificate[] getAcceptedIssuers() {
                    return new X509Certificate[0];
                }
            }
        };
        try {
            SSLContext context = SSLContext.getInstance("TLS");
            context.init(null, trustAll, new SecureRandom());
            return context;
        } catch (Exception ex) {
            throw new IllegalStateException("Failed to build trust-all SSLContext", ex);
        }
    }
}
