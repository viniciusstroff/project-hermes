UPDATE acme_registry_demo
SET name = name || ' - ' || id
WHERE name IN (
    SELECT name
    FROM acme_registry_demo
    GROUP BY name
    HAVING COUNT(name) > 1
);

UPDATE acme_people_demo
SET name = name || ' - ' || id
WHERE name || cpf IN (
    SELECT name || cpf
    FROM acme_people_demo
    GROUP BY name, cpf
    HAVING COUNT(name) > 1 AND COUNT(cpf) > 1
);
