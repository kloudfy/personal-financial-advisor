FROM maven:3.9.8-eclipse-temurin-21 AS builder
WORKDIR /app
COPY src/ledger/transactionhistory/pom.xml .
RUN mvn -q dependency:go-offline
COPY src/ledger/transactionhistory/src ./src
RUN mvn -q -DskipTests clean package

FROM gcr.io/distroless/java21:nonroot
WORKDIR /app
COPY --from=builder /app/target/transactionhistory-*.jar /app/app.jar
EXPOSE 8080
USER nonroot
ENTRYPOINT ["java","-jar","/app/app.jar"]
