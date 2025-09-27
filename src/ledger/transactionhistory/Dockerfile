FROM maven:3.9.8-eclipse-temurin-21 AS builder
WORKDIR /app
COPY src/ledger/transactionhistory/pom.xml .
RUN mvn dependency:go-offline
COPY src/ledger/transactionhistory/src ./src
RUN mvn package -DskipTests

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=builder /app/target/transactionhistory-1.0.jar .
COPY src/ledger/transactionhistory/src/main/resources .
EXPOSE 8080
CMD ["java", "-jar", "transactionhistory-1.0.jar"]