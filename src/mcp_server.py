#!/usr/bin/env python3
import asyncio
import json
import os
import sqlite3
from datetime import datetime
from typing import List, Dict, Any
import pandas as pd
from mcp import MCPServer
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types

class ManagerReportServer:
    def __init__(self, db_path: str = "/app/data/reports.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manager_name TEXT NOT NULL,
                reg_number TEXT NOT NULL,
                client TEXT NOT NULL,
                amount REAL DEFAULT 0,
                report_date DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(manager_name, reg_number, report_date)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                change_type TEXT NOT NULL,
                manager_name TEXT NOT NULL,
                reg_number TEXT NOT NULL,
                client TEXT NOT NULL,
                change_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def load_data(self, file_paths: List[str]) -> pd.DataFrame:
        all_data = []
        for file_path in file_paths:
            if os.path.exists(file_path):
                df = pd.read_excel(file_path, sheet_name='TDSheet', engine='xlrd', header=3)
                all_data.append(df)
        
        return pd.concat(all_data, ignore_index=True)
    
    def analyze_data(self, file_paths: List[str], managers: List[str]) -> Dict[str, Any]:
        try:
            df = self.load_data(file_paths)
            df_clean = df.dropna(subset=['Менеджер'])
            
            current_data = {}
            for manager in managers:
                manager_data = df_clean[df_clean['Менеджер'] == manager]
                records = []
                for _, row in manager_data.iterrows():
                    records.append({
                        'reg_number': row.get('Рег.№ и дата в нашей БД', ''),
                        'client': row.get('Клиент', ''),
                        'amount': row.get('Сумма', 0)
                    })
                current_data[manager] = records
            
            return {"success": True, "data": current_data}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_previous_data(self, managers: List[str]) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        query = '''
            SELECT manager_name, reg_number, client 
            FROM reports 
            WHERE manager_name IN ({})
        '''.format(','.join(['?'] * len(managers)))
        
        cursor = conn.cursor()
        cursor.execute(query, managers)
        results = cursor.fetchall()
        conn.close()
        
        previous_data = {}
        for manager in managers:
            previous_data[manager] = []
        
        for manager, reg_number, client in results:
            previous_data[manager].append({
                'reg_number': reg_number,
                'client': client
            })
        
        return previous_data
    
    def save_data(self, current_data: Dict[str, Any]):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM reports")
        
        for manager, records in current_data.items():
            for record in records:
                cursor.execute('''
                    INSERT INTO reports (manager_name, reg_number, client, amount, report_date)
                    VALUES (?, ?, ?, ?, ?)
                ''', (manager, record['reg_number'], record['client'], record['amount'], datetime.now().date()))
        
        conn.commit()
        conn.close()
    
    def compare_data(self, current: Dict, previous: Dict) -> Dict[str, Any]:
        comparison = {}
        for manager in current.keys():
            current_set = set((r['reg_number'], r['client']) for r in current[manager])
            previous_set = set((r['reg_number'], r['client']) for r in previous.get(manager, []))
            
            comparison[manager] = {
                'new': [{'reg_number': r[0], 'client': r[1]} for r in (current_set - previous_set)],
                'closed': [{'reg_number': r[0], 'client': r[1]} for r in (previous_set - current_set)],
                'remaining': [{'reg_number': r[0], 'client': r[1]} for r in (current_set & previous_set)]
            }
        
        return comparison

server = MCPServer("manager-reports")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="analyze_reports",
            description="Анализирует отчеты менеджеров и показывает изменения",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_paths": {"type": "array", "items": {"type": "string"}},
                    "managers": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["file_paths", "managers"]
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "analyze_reports":
        report_server = ManagerReportServer()
        
        file_paths = arguments.get("file_paths", [])
        managers = arguments.get("managers", [])
        
        current_result = report_server.analyze_data(file_paths, managers)
        if not current_result["success"]:
            return [types.TextContent(type="text", text=f"❌ {current_result['error']}")]
        
        current_data = current_result["data"]
        previous_data = report_server.get_previous_data(managers)
        
        if any(previous_data.values()):
            comparison = report_server.compare_data(current_data, previous_data)
            report_text = generate_report(comparison)
        else:
            report_text = "📊 Первая загрузка отчетов\n\n"
            for manager, records in current_data.items():
                report_text += f"{manager}: {len(records)} накладных\n"
        
        report_server.save_data(current_data)
        report_text += "\n💾 Данные сохранены"
        
        return [types.TextContent(type="text", text=report_text)]
    
    return [types.TextContent(type="text", text="Неизвестная команда")]

def generate_report(comparison: Dict[str, Any]) -> str:
    report_text = "📊 СРАВНЕНИЕ ОТЧЕТОВ\n"
    report_text += "=" * 60 + "\n"
    
    total_new = total_closed = total_remaining = 0
    
    for manager, data in comparison.items():
        report_text += f"\n{manager}:\n"
        
        if data['new']:
            report_text += f"Новых накладных - {len(data['new'])}\n"
            for i, record in enumerate(data['new'], 1):
                report_text += f"{i}). {record['reg_number']} / {record['client']}\n"
            total_new += len(data['new'])
        
        if data['closed']:
            report_text += f"\nПогасили накладные - {len(data['closed'])}:\n"
            for i, record in enumerate(data['closed'], 1):
                report_text += f"{i}). {record['reg_number']} / {record['client']}\n"
            total_closed += len(data['closed'])
        
        if data['remaining']:
            report_text += f"\nОстались не подписаны - {len(data['remaining'])}:\n"
            for i, record in enumerate(data['remaining'], 1):
                report_text += f"{i}). {record['reg_number']} / {record['client']}\n"
            total_remaining += len(data['remaining'])
        
        report_text += "-" * 40 + "\n"
    
    report_text += f"\nИТОГО:\n"
    report_text += f"Новых накладных - {total_new}\n"
    report_text += f"Погасили накладные - {total_closed}\n"
    report_text += f"Остались не подписаны - {total_remaining}\n"
    
    return report_text

async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(server_name="manager-reports", server_version="1.0.0")
        )

if __name__ == "__main__":
    asyncio.run(main())