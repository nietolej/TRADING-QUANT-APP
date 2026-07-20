from nicegui import ui
import pandas as pd
from data_layer.storage import SessionLocal, OHLCV
from ml_engine.model_trainer import MLModelTrainer

def render_ml_page():
    with ui.column().classes('w-full q-pa-md'):
        ui.label('Machine Learning Studio').classes('text-2xl font-bold text-primary q-mb-md')
        
        state = {
            'symbol': 'BTC/USDT',
            'timeframe': '4h',
            'model_type': 'Random Forest',
            'status': 'Ready',
            'metrics': None
        }

        with ui.row().classes('w-full gap-4'):
            with ui.card().classes('flex-1 q-pa-md'):
                ui.label('Dataset Configuration').classes('text-lg font-bold')
                ui.select(['BTC/USDT', 'ETH/USDT'], label='Symbol', value=state['symbol']).bind_value(state, 'symbol').classes('w-full mt-2')
                ui.select(['15m', '1h', '4h', '1d'], label='Timeframe', value=state['timeframe']).bind_value(state, 'timeframe').classes('w-full mt-2')
                ui.select(['Random Forest'], label='Algorithm', value=state['model_type']).bind_value(state, 'model_type').classes('w-full mt-2')

            with ui.card().classes('flex-1 q-pa-md'):
                ui.label('Training Status').classes('text-lg font-bold')
                status_label = ui.label(state['status']).classes('text-xl font-bold mt-4')
                
                metrics_container = ui.column().classes('w-full mt-4')
                
                async def run_training():
                    status_label.text = 'Loading Data...'
                    try:
                        # 1. Load Data
                        db = SessionLocal()
                        query = db.query(OHLCV).filter(
                            OHLCV.symbol == state['symbol'],
                            OHLCV.timeframe == state['timeframe']
                        ).order_by(OHLCV.timestamp.asc())
                        
                        df = pd.read_sql(query.statement, db.bind)
                        db.close()
                        
                        if len(df) < 100:
                            ui.notify('Not enough data. Download more OHLCV first.', type='warning')
                            status_label.text = 'Error'
                            return
                            
                        status_label.text = 'Training Model...'
                        
                        # 2. Train Model (Should be run in executor for heavy tasks, but keeping it simple)
                        trainer = MLModelTrainer(model_type=state['model_type'].lower().replace(" ", "_"))
                        metrics = trainer.train(df)
                        
                        state['metrics'] = metrics
                        status_label.text = 'Training Complete!'
                        
                        # 3. Update UI with metrics
                        metrics_container.clear()
                        with metrics_container:
                            ui.label(f"Accuracy: {metrics['accuracy']:.2%}").classes('text-green-600 font-bold')
                            ui.label(f"F1-Score: {metrics['f1_score']:.2%}").classes('text-blue-600 font-bold')
                            
                            cm = metrics['confusion_matrix']
                            ui.label('Confusion Matrix (Test Set)').classes('font-bold mt-4')
                            
                            ui.html(f'''
                            <table class="w-full text-center border collapse border-gray-300">
                                <tr class="bg-gray-100"><th class="border p-2"></th><th class="border p-2">Pred DOWN</th><th class="border p-2">Pred UP</th></tr>
                                <tr><th class="border p-2 bg-gray-100">True DOWN</th><td class="border p-2">{cm[0][0]}</td><td class="border p-2">{cm[0][1]}</td></tr>
                                <tr><th class="border p-2 bg-gray-100">True UP</th><td class="border p-2">{cm[1][0]}</td><td class="border p-2">{cm[1][1]}</td></tr>
                            </table>
                            ''').classes('w-full mt-2')
                            
                            ui.button('Save Model', on_click=lambda: save_model(trainer), color='positive').classes('w-full mt-4')
                            
                    except Exception as e:
                        ui.notify(f"Error during training: {str(e)}", type='negative')
                        status_label.text = 'Error'

                def save_model(trainer):
                    path = f"data/models/{state['symbol'].replace('/', '_')}_{state['timeframe']}_model.pkl"
                    trainer.save_model(path)
                    ui.notify(f"Model saved to {path}", type='positive')

                ui.button('Start Training', on_click=run_training, color='primary').classes('w-full mt-6')
