update
    registries
    set name = name || ' - ' || id where name in
    (
        SELECT name FROM
        registries
        GROUP BY name HAVING COUNT(f_name) > 1
    ); 

    
    update
	participants
set name = name || ' - ' || id where name || cpf in
(
	SELECT name || cpf FROM participants
 	GROUP BY name,cpf HAVING COUNT(name)>1 and COUNT(cpf)>1
 );
 