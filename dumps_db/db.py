import sqlalchemy
import os

from sqlalchemy.orm import sessionmaker


class DumpsDB:
    __tablename__ = 'Dumps'

    def __init__(self):
        self.engine = sqlalchemy.create_engine(f"sqlite:///{os.environ['TEMP']}/dumps.db")
        self.connection = self.engine.connect()

        Session = sessionmaker(bind=self.engine)
        self.session = Session()

        metadata = sqlalchemy.MetaData()

        self.dumps_table = sqlalchemy.Table('Dumps',
                                            metadata,
                                            sqlalchemy.Column('dump', sqlalchemy.String(60), nullable=False, unique=True)
                                            )

        metadata.create_all(self.engine)

    def check_exist(self, dump):
        return self.connection.execute(sqlalchemy.select([self.dumps_table]).where(self.dumps_table.columns.dump == dump)).fetchall()

    def insert(self, dump_name):
        query = sqlalchemy.insert(self.dumps_table)
        self.connection.execute(query, [{"dump": dump_name}])

    def delete(self, dump_name):
        query = sqlalchemy.delete(self.dumps_table)
        self.connection.execute(query, [{"dump": dump_name}])

    @property
    def all_values(self):
        return [item[0] for item in self.session.query(self.dumps_table).all()]
