import requests
from bs4 import BeautifulSoup
import urllib.parse
import datetime
from datetime import date
import re
import os
import glob
import pandas as pd

type_map = {
    'SPLOŠNA DEJAVNOST - SPLOŠNA AMBULANTA': 'gp',
    'SPLOŠNA DEJ.-OTROŠKI IN ŠOLSKI DISPANZER': 'gp-y',
    'ZOBOZDR. DEJAVNOST-ZDRAVLJENJE ODRASLIH': 'den',
    'ZOBOZDR. DEJAVNOST-ZDRAVLJENJE MLADINE': 'den-y',
    'ZOBOZDR. DEJAVNOST-ZDRAVLJENJE ŠTUDENTOV': 'den-s',
    'SPLOŠNA DEJAVNOST - DISPANZER ZA ŽENSKE': 'gyn'
}

accepts_map = {
    'DA': 'y',
    'NE': 'n'
}

def convert_to_csv():
    doctors = []
    for group in ["zdravniki", "zobozdravniki", "ginekologi"]:
        filename = max(glob.glob(f"zzzs/*_{group}.xlsx"))
        print(f"Source: {group} - {filename}")

        df = pd.read_excel(io=filename, sheet_name='Podatki', skiprows=9).dropna()
        df.columns = ['unit', 'name', 'address', 'city', 'doctor', 'type', 'availability', 'load', 'accepts']
        df['doctor'] = df['doctor'].str.strip().replace('\s+', ' ', regex=True)
        df['doctor'] = df['doctor'].str.title()
        df['type'] = df['type'].str.strip().map(type_map)
        df['accepts'] = df['accepts'].str.strip().map(accepts_map)
        df['name'] = df['name'].str.strip()
        df['address'] = df['address'].str.strip()
        df['city'] = df['city'].str.strip()
        df['unit'] = df['unit'].str.strip()
        df = df.reindex(['doctor', 'type', 'accepts', 'availability', 'load', 'name', 'address', 'city', 'unit'], axis='columns')
        doctors.append(df)

    doctors = pd.concat(doctors, ignore_index=True)

    institutions = doctors.groupby(['name','address','city', 'unit'])['doctor'].apply(list).reset_index()
    institutions.drop("doctor", axis='columns', inplace=True)

    institutions.index.rename('id_inst', inplace=True)
    institutions.to_csv('csv/dict-institutions.csv')

    doctors.drop(['address', 'city', 'unit'], axis='columns', inplace=True)
    institutions.drop(['address', 'city', 'unit'], axis='columns', inplace=True)
    institutions = institutions.reset_index().set_index('name')
    doctors = doctors.join(institutions, on='name')
    doctors.drop('name', axis='columns', inplace=True)

    doctors.index.rename('id', inplace=True)
    doctors.to_csv('csv/doctors.csv')


def add_geodata():
    institutions = pd.read_csv('csv/dict-institutions.csv', index_col=['id_inst'])
    dfgeo=pd.read_csv('csv/dict-geodata.csv', index_col=['cityZZZS','addressZZZS'])
    dfgeo.fillna('', inplace=True)
    dfgeo['address'] = dfgeo.apply(lambda x: f'{x.street} {x.housenumber}{x.housenumberAppendix}', axis = 1)
    dfgeo['post'] = dfgeo.apply(lambda x: f'{x.zipCode} {x.zipName}', axis = 1)

    institutions = institutions.merge(dfgeo[['address','post','city','municipality','lat','lon']], how = 'left', left_on = ['city','address'], right_index=True, suffixes=['_zzzs', ''])
    institutions.drop(['address_zzzs','city_zzzs'], axis='columns', inplace=True)

    institutions.to_csv('csv/dict-institutions.csv')


def add_zzzs_api_data():
    # keys for ZZZS API calls, add as needed, see https://www.zzzs.si/zzzs-api/izvajalci-zdravstvenih-storitev/po-dejavnosti/
    zzzsApiKeys=[
        'Splošna ambulanta',
        'Otroški in šolski dispanzer',
        'Zobozdravstvo za odrasle',
        'Zobozdravstvo za mladino',
        'Zobozdravstvo za študente',
        'Dispanzer za ženske'
    ]

    apiInstitutions = []
    for key in zzzsApiKeys:
        print(f"Fetching from ZZZS API: {key}")
        apiUrl = f"https://www.zzzs.si/zzzs-api/izvajalci-zdravstvenih-storitev/po-dejavnosti/?ajax=1&act=get-izvajalci&type=dejavnosti&key={key}"
        r = requests.get(apiUrl)
        j = r.json()
        df = pd.DataFrame.from_dict(j).set_index('naziv')
        apiInstitutions.append(df)

    apiInstitutions = pd.concat(apiInstitutions).drop_duplicates()
    print(apiInstitutions)

    institutions = pd.read_csv('csv/dict-institutions.csv', index_col=['id_inst'])
    institutions = institutions.merge(apiInstitutions[['tel','splStran']], how = 'left', left_on = ['name'], right_index=True, suffixes=['', '_api'])
    institutions.index.rename('id_inst', inplace=True)
    institutions.rename(columns={"tel": "phone", "splStran": "website"}, inplace=True)

    print(institutions)
    institutions.to_csv('csv/dict-institutions.csv')


def download_nijz_xlsx_files():
    # Število opredeljenih pri aktivnih zobozdravnikih na dan 01.11.2020
    # Število opredeljenih pri aktivnih zdravnikih na dan 01.11.2020
    # Število opredeljenih pri aktivnih ginekologih na dan 3.1.2021
    nameRegex= r".* (zobozdravniki|zdravniki|ginekologi).* ([0-9]{1,2}\.[0-9]{1,2}\.20[0-9]{2})"

    BaseURL = "https://zavarovanec.zzzs.si/wps/portal/portali/azos/ioz/ioz_izvajalci"
    page = requests.get(BaseURL)
    soup = BeautifulSoup(page.content, "html.parser")
    ultag = soup.find("ul", class_="datoteke")

    for litag in ultag.find_all('li'):
        atag=litag.find('a')
        title=atag.text
        print(title)

        match = re.match(nameRegex, title)
        if match == None:
            print(f"Unexpected title '{title}' not matching regex '{nameRegex}''.")
            raise

        date = datetime.datetime.strptime(match.group(2), '%d.%m.%Y').date()
        group = match.group(1).lower()
        filename = f"{date}_{group}.xlsx"
        dest = os.path.join("zzzs/",filename)

        if os.path.exists(dest):
            print(f"    Already downloaded: {dest}")
        else:
            h=atag['href'].strip()
            url=urllib.parse.urljoin(BaseURL,h)

            r = requests.get(url, allow_redirects=True)
            r.raise_for_status()
            ct = r.headers.get('content-type')
            if ct.lower() != "application/xlsx":
                print(f"Unexpected content type '{ct}'.")
                raise

            print(f"    Saving to: {dest}")

            open(dest, 'wb').write(r.content)


if __name__ == "__main__":
    download_nijz_xlsx_files()
    convert_to_csv()
    add_geodata()
    add_zzzs_api_data()
