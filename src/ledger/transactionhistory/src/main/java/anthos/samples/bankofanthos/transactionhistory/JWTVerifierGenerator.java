/*
 * Copyright 2020, Google LLC.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES, CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package anthos.samples.bankofanthos.transactionhistory;

import com.auth0.jwt.JWT;
import com.auth0.jwt.JWTVerifier;
import com.auth0.jwt.algorithms.Algorithm;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.security.KeyFactory;
import java.security.NoSuchAlgorithmException;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.InvalidKeySpecException;
import java.security.spec.X509EncodedKeySpec;
import java.util.Base64;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class JWTVerifierGenerator {

    private static final Logger LOGGER =
        LogManager.getLogger(JWTVerifierGenerator.class);

    @Bean (name = "verifier")
    public JWTVerifier generateJWTVerifier(
            @Value("${PUB_KEY_PATH}") final String publicKeyPath,
            @Value("${JWT_ISSUER:userservice}") final String issuer) {
        // load public key from file
        try {
            LOGGER.info("Generating JWT token verifier with PUB_KEY_PATH={} and JWT_ISSUER={}", publicKeyPath, issuer);
            String keyStr =
                    new String(Files.readAllBytes(Paths.get(publicKeyPath)));
            LOGGER.debug("Raw public key: {}", keyStr);
            keyStr = keyStr.replaceFirst("-----BEGIN PUBLIC KEY-----", "")
                    .replaceFirst("-----END PUBLIC KEY-----", "")
                    .replaceAll("\\s", "");
            if (keyStr.isEmpty()) {
                LOGGER.error("Public key is empty after processing");
                throw new GenerateKeyException("Public key is empty", null);
            }
            byte[] keyBytes = Base64.getDecoder().decode(keyStr);
            KeyFactory kf = KeyFactory.getInstance("RSA");
            X509EncodedKeySpec keySpecX509 = new X509EncodedKeySpec(keyBytes);
            RSAPublicKey publicKey =
                    (RSAPublicKey) kf.generatePublic(keySpecX509);
            // Initialize JWT verifier
            Algorithm algorithm = Algorithm.RSA256(publicKey, null);
            JWTVerifier verifier = JWT.require(algorithm)
                                     .withIssuer(issuer)
                                     .build();
            LOGGER.info("JWT verifier created successfully");
            return verifier;
        } catch (IOException
                | NoSuchAlgorithmException
                | InvalidKeySpecException e) {
            LOGGER.error("Failed initializing JWT verifier: {}", e.toString());
            throw new GenerateKeyException("Cannot generate key: ", e);
        }
    }

    public static class GenerateKeyException extends RuntimeException {
        public GenerateKeyException(String message, Exception e) {
            super(message, e);
        }
    }
}